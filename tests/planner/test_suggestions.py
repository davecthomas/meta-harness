"""The planner drafts fixes to a plan's gaps, for the operator to accept or dismiss.

The planner's API runs against Postgres with fakes for the tracker, the Task API and the event service. The
model that sorts a plan's gaps is a fake assessor; no model is called.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from meta_harness.planner import planning, suggestions, updates
from meta_harness.planner.service import create_app

from .test_planner import OPERATOR, Events, FakeTasks, _no_title, migrated, registry  # noqa: F401
from .test_planning import WritingTracker, _feature, draft

CLASH = "Store and api both edit app.py, so they may clash."
OPEN = "How many exports a day the team expects is not known."


class Assessor:
    def __init__(self, fixes: list[dict] | None = None):
        self.fixes = fixes if fixes is not None else [{"gap": CLASH, "change": "Run api after store."}]
        self.calls = 0

    async def __call__(self, plan):
        self.calls += 1
        return self.fixes, "claude:test-model", "0.0007"


@pytest.fixture
def world():
    events, tracker, tasks = Events(), WritingTracker(), FakeTasks()
    app = create_app(
        events, tracker, tasks, operator_keys={"op-key": "dave"}, service_keys={}, sweep_seconds=0, titler=_no_title
    )
    with TestClient(app) as client:
        yield client, events, tasks


async def _no_issues(goal):
    return []


def _end(events, tasks, task: dict, drafts: list[dict], gaps: list[str]) -> None:
    """The planning task ends having drafted these, with this summary's gaps."""
    for d in drafts:
        events.emit("issue_drafted", d, task_id=task["id"])
    events.emit("plan_summarized", {"summary": "Store, then api.", "gaps": gaps}, task_id=task["id"])
    task["status"] = "closed"


def _planned(client, events, tasks, gaps=(CLASH, OPEN)) -> tuple[dict, dict]:
    goal = _feature(client)
    task = next(t for t in tasks.tasks.values() if t.get("goal_id") == goal["id"])
    _end(events, tasks, task, [draft("store"), draft("api")], list(gaps))
    shown = client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()
    return client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json(), shown["plan"]


def _start(goal, plan, tasks, events, assessor) -> dict | None:
    return asyncio.run(suggestions.maybe_start(goal, plan, tasks, events, _no_issues, assessor))


def test_a_plan_with_a_gap_the_planner_can_fix_gets_a_drafted_fix_to_accept(world):
    client, events, tasks = world
    goal, plan = _planned(client, events, tasks)
    assessor = Assessor()

    task = _start(goal, plan, tasks, events, assessor)

    assert task["task_type"] == "plan" and task["external_ref"] == f"plan-fix:{goal['id']}:1"
    assert "Run api after store." in task["description"] and "change nothing else" in task["description"]
    assert (
        "plan_fixes_assessed",
        {"goal_id": goal["id"], "version": 1, "fixes": 1, "model": "claude:test-model", "usd_cost": "0.0007"},
    ) in events.events
    # The goal's own plan is not being redrafted: it can still be approved, and its page shows no drafting.
    shown = client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()
    assert shown["planning_task_id"] is None and shown["suggestion"] is None
    assert _start(goal, plan, tasks, events, assessor) is None and assessor.calls == 1, "one fix per plan version"

    # The fix records the one task it changes. The task it does not name is the plan's own, unchanged.
    assert "--- the plan so far ---" in task["description"]
    _end(events, tasks, task, [draft("api", ("store",))], [OPEN])
    assert asyncio.run(suggestions.collect(goal, tasks, events))["status"] == "open"
    suggestion = client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()["suggestion"]
    assert suggestion["for_version"] == 1 and suggestion["fixes"] == [{"gap": CLASH, "change": "Run api after store."}]
    assert suggestion["changes"] == ["Build api now waits for Build store"]

    accepted = client.post(f"/v1/goals/{goal['id']}/suggestion/accept", headers=OPERATOR)

    assert accepted.status_code == 200, accepted.text
    plan2 = accepted.json()["plan"]
    assert (plan2["version"], plan2["source"], plan2["gaps"]) == (2, "suggested", [OPEN])
    assert plan2["drafts"][1]["depends_on"] == ["store"] and plan2["note"].startswith("The planner's suggested fix")
    assert accepted.json()["suggestion"] is None
    assert "plan_fix_accepted" in events.types()
    # A version that was itself a fix gets none of its own.
    assert _start(goal, plan2, tasks, events, Assessor()) is None
    assert client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR, json={"track": True}).status_code == 200


def test_a_fix_can_be_dismissed_and_one_for_an_older_plan_is_not_offered(world):
    client, events, tasks = world
    goal, plan = _planned(client, events, tasks)
    task = _start(goal, plan, tasks, events, Assessor())
    _end(events, tasks, task, [draft("store"), draft("api", ("store",))], [])
    asyncio.run(suggestions.collect(goal, tasks, events))

    dismissed = client.post(f"/v1/goals/{goal['id']}/suggestion/dismiss", headers=OPERATOR)
    assert dismissed.status_code == 200 and dismissed.json()["suggestion"] is None
    again = client.post(f"/v1/goals/{goal['id']}/suggestion/dismiss", headers=OPERATOR)
    assert again.status_code == 409 and "nothing to dismiss" in again.json()["detail"]
    refused = client.post(f"/v1/goals/{goal['id']}/suggestion/accept", headers=OPERATOR)
    assert refused.status_code == 409 and "Ask for an update" in refused.json()["detail"]


def test_an_edit_leaves_a_fix_behind(world):
    client, events, tasks = world
    goal, plan = _planned(client, events, tasks)
    task = _start(goal, plan, tasks, events, Assessor())
    _end(events, tasks, task, [draft("store"), draft("api", ("store",))], [])
    asyncio.run(suggestions.collect(goal, tasks, events))
    assert client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()["suggestion"]

    edited = client.put(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR, json={"drafts": [draft("store")]})

    assert edited.status_code == 200 and edited.json()["suggestion"] is None, "it was drafted for version 1"


def test_no_fix_is_drafted_when_none_is_needed_or_fixes_are_off(world, monkeypatch):
    client, events, tasks = world
    goal, plan = _planned(client, events, tasks, gaps=(OPEN,))
    assert _start(goal, plan, tasks, events, Assessor(fixes=[])) is None
    assert not [t for t in tasks.tasks.values() if str(t["external_ref"]).startswith("plan-fix:")]

    goal2, plan2 = _planned(client, events, tasks, gaps=())
    never = Assessor()
    assert _start(goal2, plan2, tasks, events, never) is None and never.calls == 0, "no gaps, no call"

    shipped = planning.settings()
    monkeypatch.setattr(planning, "settings", lambda: {**shipped, "suggest_fixes": False})
    goal3, plan3 = _planned(client, events, tasks)
    assert _start(goal3, plan3, tasks, events, never) is None and never.calls == 0


def test_the_changes_a_fix_makes_read_a_line_each():
    before = {
        "drafts": [draft("store"), draft("api"), draft("docs")],
        "items": [{"ref": "jira:VL-1", "decision": "keep", "depends_on": []}],
        "issues": [{"ref": "jira:VL-1", "repo": "o/r", "title": "Crash on save"}],
    }
    after = {
        "drafts": [draft("store"), draft("api", ("store",)), draft("tests")],
        "items": [{"ref": "jira:VL-1", "decision": "leave_out", "depends_on": []}],
    }
    assert suggestions.changes(before, after) == [
        "Adds: Build tests",
        "Drops: Build docs",
        "Build api now waits for Build store",
        "Crash on save is left out",
    ]


def test_a_waiting_fix_is_always_asked_about_with_what_it_changes():
    suggestion = {"fixes": [], "changes": ["Build api now waits for Build store"]}
    needs = updates.actionable(
        [{"what": "Approve the plan.", "ref": "", "action": "approve_plan"}], {"status": "planning"}, [], [], suggestion
    )

    assert [n["action"] for n in needs] == ["accept_fix", "approve_plan"]
    assert needs[0]["changes"] == ["Build api now waits for Build store"]
    assert (
        updates.actionable(
            [{"what": "Accept the fix.", "ref": "", "action": "accept_fix"}], {"status": "planning"}, [], [], None
        )[0]["action"]
        == "approve_plan"
    )
