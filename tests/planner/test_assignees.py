# Created by the meta-harness in run t-ad0990f39f2b:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""The planner assigns each issue it creates or starts to the person the goal names, or to its owner
(assignees.py).

The planner's API runs against Postgres with fakes for the tracker, the Task API and the event service. No
model and no GitHub is called.
"""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from meta_harness.planner import assignees, sizing, updates
from meta_harness.planner.service import create_app
from meta_harness.trackers.base import unknown_person

from .test_planner import OPERATOR, Events, FakeTasks, _label, _no_title, _number, migrated, registry  # noqa: F401
from .test_planning import WritingTracker, _feature, _planned, draft
from .test_updates import Writer

REPO = "davecthomas/meta-harness"
OWNER = "dave@example.com"
CHECKS = "## Acceptance checks\n\n- make test"


class AssigningTracker(WritingTracker):
    """A tracker that lists people and assigns its issues, as the adapters do: a person it does not know is
    refused before anything changes, and an issue with an assignee keeps it."""

    def __init__(self):
        super().__init__()
        # The people who can be assigned, by id, with their name and email as Jira shows them.
        self.people = {"dave-gh": f"Dave ({OWNER})", "ana": "Ana (ana@example.com)"}
        self.assigned: dict[str, str] = {}
        self.assign_calls: list[tuple[str, str]] = []

    async def choices(self, setting, scope=None):
        return [{"value": person, "label": label} for person, label in self.people.items()]

    async def assignee(self, ref):
        return self.assigned.get(ref, "")

    async def assign(self, ref, person):
        self.assign_calls.append((ref, person))
        if person not in self.people:
            raise unknown_person(ref, person, "the people who can be assigned issues")
        if self.assigned.get(ref):
            return False
        self.assigned[ref] = person
        return True


class Sizer:
    """Sizes the refs in `large` large, so they are held for a split, and every other issue small."""

    def __init__(self):
        self.large: set[str] = set()

    async def __call__(self, issue):
        size = "large" if issue.ref in self.large else "small"
        answer = {"size": size, "reasons": [], "missing": [], "split": ["One", "Two"] if size == "large" else []}
        return sizing.parse(answer, issue, "openai-decisions:gpt-6-luna", "0.0002")


@pytest.fixture
def world(monkeypatch):
    monkeypatch.delenv("MH_CONFIG_SOURCE", raising=False)
    events, tracker, tasks, sizer = Events(), AssigningTracker(), FakeTasks(), Sizer()
    tracker.sizer = sizer
    app = create_app(
        events,
        tracker,
        tasks,
        operator_keys={"op-key": OWNER},
        service_keys={},
        sweep_seconds=0,
        titler=_no_title,
        sizer=sizer,
    )
    with TestClient(app) as client:
        yield client, events, tracker, tasks


def _payloads(events, event_type: str) -> list[dict]:
    return [p for t, p in events.events if t == event_type]


def _goal(client, **fields) -> dict:
    body = {"statement": "Fix bugs", "repos": [REPO], "label": _label(), **fields}
    created = client.post("/v1/goals", json=body, headers=OPERATOR)
    assert created.status_code == 201, created.text
    goal = created.json()
    assert client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR).status_code == 200
    return goal


def _sweep(client, goal: dict) -> dict:
    swept = client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR)
    assert swept.status_code == 200, swept.text
    return swept.json()


def _task(tasks, ref: str) -> dict:
    return next(t for t in tasks.tasks.values() if t["external_ref"] == ref)


def test_a_plans_issues_are_assigned_to_the_goals_owner_as_they_are_created(world):
    client, events, tracker, tasks = world
    goal = _feature(client)
    _planned(client, events, tasks, goal, [draft("store"), draft("api", ("store",))])

    approved = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR)

    assert approved.status_code == 200, approved.text
    refs = [issue.ref for issue in tracker.created]
    assert len(refs) == 2
    assert tracker.assigned == {ref: "dave-gh" for ref in refs}, "the owner is found by email among the people"
    assert _payloads(events, "issue_not_assigned") == []


def test_a_splits_parts_are_assigned_as_they_are_created(world):
    client, events, tracker, tasks = world
    goal = _goal(client)
    ref = tracker.add(REPO, _number(), body=f"Everything.\n\n{CHECKS}", labels=[goal["label"]])
    # Held for its size, so the operator splits it.
    tracker.sizer.large.add(ref)
    _sweep(client, goal)
    parts = [{"title": t, "body": f"Part {t}.\n\nPaths: console\n\n{CHECKS}"} for t in ("Titles", "Labels")]

    split = client.post(f"/v1/goals/{goal['id']}/issues/split", json={"ref": ref, "issues": parts}, headers=OPERATOR)

    assert split.status_code == 201, split.text
    created = split.json()["created"]
    assert len(created) == 2
    assert {r: tracker.assigned.get(r) for r in created} == {r: "dave-gh" for r in created}
    assert ref not in tracker.assigned, "the issue split is not assigned by the split"


def test_a_taken_in_issue_is_assigned_when_its_task_starts_and_one_with_an_assignee_keeps_it(world):
    client, events, tracker, tasks = world
    goal = _goal(client)
    free = tracker.add(REPO, _number(), body=f"Fix it.\n\n{CHECKS}", labels=[goal["label"]])
    taken = tracker.add(REPO, _number(), body=f"Fix that.\n\n{CHECKS}", labels=[goal["label"]])
    tracker.assigned[taken] = "someone-else"

    _sweep(client, goal)
    assert free not in tracker.assigned, "a queued task has not started: its issue waits"

    for ref in (free, taken):
        _task(tasks, ref)["status"] = "running"
    _sweep(client, goal)

    assert tracker.assigned[free] == "dave-gh"
    assert tracker.assigned[taken] == "someone-else"
    assert all(ref != taken for ref, _ in tracker.assign_calls), "an issue with an assignee is not asked to change"
    calls = len(tracker.assign_calls)
    _sweep(client, goal)
    assert len(tracker.assign_calls) == calls, "each issue is looked at once"


def test_a_person_the_tracker_does_not_know_is_reported_and_the_issue_is_still_created(world):
    client, events, tracker, tasks = world
    goal = _feature(client, tracker="github", tracker_settings={"assignee": "ghost"})
    _planned(client, events, tasks, goal, [draft("store")])

    approved = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR)

    assert approved.status_code == 200, approved.text
    [issue] = tracker.created
    assert tracker.assign_calls == [(issue.ref, "ghost")] and tracker.assigned == {}
    [reported] = _payloads(events, "issue_not_assigned")
    assert (reported["goal_id"], reported["ref"], reported["person"]) == (goal["id"], issue.ref, "ghost")
    assert "ghost" in reported["reason"] and "Add ghost to the tracker" in reported["todo"]
    after = client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()
    assert after["status"] == "active" and issue.ref in {i["ref"] for i in after["issues"]}

    # The goal's page asks the operator about it.
    links = [{"ref": issue.ref, "state": "open"}]
    found = assignees.not_assigned(events, goal["id"], links)
    assert found == [{"ref": issue.ref, "person": "ghost", "todo": reported["todo"]}]
    [need] = assignees.needs(found, [])
    assert (need["ref"], need["action"]) == (issue.ref, "open_issue") and "not assigned to ghost" in need["what"]
    assert assignees.needs(found, [{"ref": issue.ref}]) == [], "a need about the issue already is not repeated"
    assert updates.digest({**_facts(), "not_assigned": found}) != updates.digest(_facts())
    writer = Writer()
    written = asyncio.run(updates.write(after, tasks, events, writer))
    assert writer.calls[0][0]["not_assigned"] == found, "the update's writer is told"
    assert [n["ref"] for n in written["needs_you"] if "not assigned to ghost" in n["what"]] == [issue.ref]


def _facts() -> dict:
    return {"goal": {"status": "active"}, "plan": None, "issues": [], "unstuck": []}


def test_who_is_worked_out_from_the_setting_then_the_owner_then_nobody():
    tracker = AssigningTracker()

    def who(**goal) -> assignees.Who:
        goal = {"id": f"g-{uuid.uuid4().hex[:8]}", "repos": [REPO], "created_by": OWNER, **goal}
        return asyncio.run(assignees.person_for(goal, tracker, f"github:{REPO}#1", REPO))

    assert who(tracker="github", tracker_settings={"assignee": "ana"}) == assignees.Who("ana", "setting")
    assert who(tracker="github") == assignees.Who("dave-gh", "owner")
    # The setting names a person of the goal's own tracker: an issue kept in the meta-harness goes to the owner.
    kept = {"id": f"g-{uuid.uuid4().hex[:8]}", "repos": [REPO], "created_by": OWNER, "tracker": "github"}
    kept["tracker_settings"] = {"assignee": "ana"}
    assert asyncio.run(assignees.person_for(kept, tracker, "mh:abc", REPO)) == assignees.Who(OWNER, "owner")
    nobody = who(tracker="github", created_by="zed@example.com")
    assert nobody.person == "" and "zed@example.com" in nobody.why
    assert who(tracker="github", created_by="dave").person == "", "an owner with no email matches no one"
