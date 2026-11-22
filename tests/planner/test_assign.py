# Created by the meta-harness in run t-d07e0a1296b3:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""The planner assigns the issues it creates and starts through the trackers' assign call, matching the goal's
owner among the people of each issue's own repo (`people(repo)`, assignees.py).

The planner's API runs against Postgres with a fake tracker shaped like the adapters: it lists people by repo
and assigns, with no `choices`. No model and no GitHub is called.
"""

import pytest
from fastapi.testclient import TestClient

from meta_harness.planner.service import create_app
from meta_harness.trackers.base import unknown_person

from .test_assignees import CHECKS, OWNER, REPO, Sizer, _goal, _payloads, _sweep, _task
from .test_planner import OPERATOR, Events, FakeTasks, _label, _no_title, _number, migrated, registry  # noqa: F401
from .test_planning import WritingTracker, _feature, _planned, draft

OTHER = "davecthomas/ai-api-unified"


class PeopleTracker(WritingTracker):
    """Lists the people each repo can assign and assigns them, as the adapters do: a person the issue's repo
    cannot assign is refused before anything changes, and an issue with an assignee keeps it."""

    def __init__(self):
        super().__init__()
        self.by_repo = {REPO: {"dave-gh": f"Dave ({OWNER})"}, OTHER: {"ana": "Ana (ana@example.com)"}}
        self.assigned: dict[str, str] = {}
        self.assign_calls: list[tuple[str, str]] = []
        self.people_calls: list[str] = []

    async def people(self, repo):
        self.people_calls.append(repo)
        return [{"value": p, "label": label} for p, label in self.by_repo.get(repo, {}).items()]

    async def assignee(self, ref):
        return self.assigned.get(ref, "")

    async def assign(self, ref, person):
        self.assign_calls.append((ref, person))
        if person not in self.by_repo.get(self.issues[ref].repo, {}):
            raise unknown_person(ref, person, "the people who can be assigned issues")
        if self.assigned.get(ref):
            return False
        self.assigned[ref] = person
        return True


@pytest.fixture
def world(monkeypatch):
    monkeypatch.delenv("MH_CONFIG_SOURCE", raising=False)
    events, tracker, tasks, sizer = Events(), PeopleTracker(), FakeTasks(), Sizer()
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


def test_a_plans_issue_is_assigned_when_it_is_created(world):
    client, events, tracker, tasks = world
    goal = _feature(client)
    _planned(client, events, tasks, goal, [draft("store")])

    approved = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR)

    assert approved.status_code == 200, approved.text
    [issue] = tracker.created
    assert tracker.assigned == {issue.ref: "dave-gh"}, "the owner is matched by email among the repo's people"
    assert tracker.people_calls == [REPO]
    assert _payloads(events, "issue_not_assigned") == []


def test_each_issue_is_matched_among_its_own_repos_people(world):
    client, events, tracker, tasks = world
    goal = _feature(client, repos=[REPO, OTHER])
    _planned(client, events, tasks, goal, [draft("store"), draft("client", repo=OTHER)])

    approved = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR)

    assert approved.status_code == 200, approved.text
    by_repo = {issue.repo: issue.ref for issue in tracker.created}
    assert tracker.assigned == {by_repo[REPO]: "dave-gh"}
    assert sorted(tracker.people_calls) == sorted([REPO, OTHER])
    assert all(ref != by_repo[OTHER] for ref, _ in tracker.assign_calls), "no one in that repo has the email"
    assert _payloads(events, "issue_not_assigned") == [], "an owner matched to no one is not reported"


def test_a_split_part_is_assigned_when_it_is_created(world):
    client, events, tracker, tasks = world
    goal = _goal(client)
    ref = tracker.add(REPO, _number(), body=f"Everything.\n\n{CHECKS}", labels=[goal["label"]])
    tracker.sizer.large.add(ref)
    _sweep(client, goal)
    parts = [{"title": t, "body": f"Part {t}.\n\nPaths: console\n\n{CHECKS}"} for t in ("Titles", "Labels")]

    split = client.post(f"/v1/goals/{goal['id']}/issues/split", json={"ref": ref, "issues": parts}, headers=OPERATOR)

    assert split.status_code == 201, split.text
    created = split.json()["created"]
    assert len(created) == 2
    assert {r: tracker.assigned.get(r) for r in created} == {r: "dave-gh" for r in created}
    assert ref not in tracker.assigned


def test_a_taken_in_issue_is_assigned_at_task_start_and_an_assigned_one_is_not_changed(world):
    client, events, tracker, tasks = world
    goal = _goal(client)
    free = tracker.add(REPO, _number(), body=f"Fix it.\n\n{CHECKS}", labels=[goal["label"]])
    taken = tracker.add(REPO, _number(), body=f"Fix that.\n\n{CHECKS}", labels=[goal["label"]])
    tracker.assigned[taken] = "someone-else"

    _sweep(client, goal)
    assert free not in tracker.assigned, "a queued task has not started"

    for ref in (free, taken):
        _task(tasks, ref)["status"] = "running"
    _sweep(client, goal)

    assert tracker.assigned == {free: "dave-gh", taken: "someone-else"}
    assert [ref for ref, _ in tracker.assign_calls] == [free]


def test_an_unknown_person_is_reported_and_the_issue_is_still_created(world):
    client, events, tracker, tasks = world
    goal = _feature(client, tracker="github", tracker_settings={"assignee": "ghost"})
    _planned(client, events, tasks, goal, [draft("store")])

    approved = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR)

    assert approved.status_code == 200, approved.text
    [issue] = tracker.created
    assert tracker.assign_calls == [(issue.ref, "ghost")] and tracker.assigned == {}
    assert tracker.people_calls == [], "a named person is not looked up: assign says whether the tracker knows them"
    [reported] = _payloads(events, "issue_not_assigned")
    assert (reported["goal_id"], reported["ref"], reported["person"]) == (goal["id"], issue.ref, "ghost")
    assert "Add ghost to the tracker" in reported["todo"]
    after = client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()
    assert after["status"] == "active" and issue.ref in {i["ref"] for i in after["issues"]}
