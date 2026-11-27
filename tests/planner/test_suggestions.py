"""The planner drafts fixes to a plan's gaps, for the operator to accept or dismiss.

The planner's API runs against Postgres with fakes for the tracker, the Task API and the event service. The
model that sorts a plan's gaps is a fake assessor; no model is called.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from meta_harness import db
from meta_harness.planner import plans, planning, suggestions, updates
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


@pytest.fixture
def waiting(monkeypatch):
    """A fix that waits to be accepted, as one does when its plan is no longer the planner's own: a plan nobody
    has touched takes its fix by itself."""
    monkeypatch.setattr(suggestions, "_untouched", lambda goal, version: False)


def _start(goal, plan, tasks, events, assessor) -> dict | None:
    return asyncio.run(suggestions.maybe_start(goal, plan, tasks, events, _no_issues, assessor))


def test_a_plan_with_a_gap_the_planner_can_fix_gets_a_drafted_fix_to_accept(world, waiting):
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
    assert suggestion["changes"] == ["1 task reordered"], "a count for each kind of change, never a line a task"
    assert suggestion["diff"]["reordered"] == [{"id": "api", "title": "Build api"}]

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


def test_a_fix_can_be_dismissed_and_one_for_an_older_plan_is_not_offered(world, waiting):
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


def test_an_edit_leaves_a_fix_behind(world, waiting):
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
        "1 task added",
        "1 task removed",
        "1 task reordered",
        "1 existing issue decided differently",
    ]
    found = plans.diff(before, after)
    assert found["added"] == [{"id": "tests", "title": "Build tests"}]
    assert found["decisions"] == [{"id": "jira:VL-1", "title": "Crash on save", "decision": "leave_out"}]
    # A task is in one kind: its checks changed, or only its words.
    checks = draft("api")
    checks["body"] = checks["body"].replace("- make test", "- make test-api")
    words = draft("store", title="The store")
    found = plans.diff({"drafts": [draft("store"), draft("api")]}, {"drafts": [words, checks]})
    assert [e["id"] for e in found["changed"]] == ["api"] and [e["id"] for e in found["reworded"]] == ["store"]
    assert plans.diff_lines(found) == ["1 task changed", "1 task reworded"]
    assert plans.diff_lines(plans.diff(before, before)) == ["No task changes"]


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


def test_a_fix_to_a_plan_nobody_has_touched_applies_itself_and_can_be_undone(world):
    """Accepting a fix to a plan the operator had not read yet, and then approving the result, were two
    decisions where one will do."""
    client, events, tasks = world
    goal, plan = _planned(client, events, tasks)
    task = _start(goal, plan, tasks, events, Assessor())
    _end(events, tasks, task, [draft("api", ("store",))], [OPEN])
    goal = client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()

    collected = asyncio.run(suggestions.collect(goal, tasks, events))

    assert collected["status"] == "accepted" and collected["accepted_version"] == 2
    shown = client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()
    assert (shown["plan"]["version"], shown["plan"]["source"], shown["plan"]["created_by"]) == (
        2,
        "suggested",
        "planner",
    )
    assert shown["suggestion"] is None, "nothing is left to accept: the one decision is to approve"
    fix = shown["applied_fix"]
    assert fix["from_version"] == 1 and fix["fixes"] == [{"gap": CLASH, "change": "Run api after store."}]
    assert fix["changes"] == ["1 task reordered"]
    assert "plan_fix_accepted" in events.types()

    # A person can always decide differently: undo puts back the plan before the fix, as a version of theirs.
    undone = client.post(f"/v1/goals/{goal['id']}/plan/undo-fix", headers=OPERATOR)

    assert undone.status_code == 200, undone.text
    back = undone.json()
    assert (back["plan"]["version"], back["plan"]["source"], back["plan"]["created_by"]) == (3, "edited", "dave")
    assert back["plan"]["drafts"] == plan["drafts"] and back["applied_fix"] is None
    again = client.post(f"/v1/goals/{goal['id']}/plan/undo-fix", headers=OPERATOR)
    assert again.status_code == 409 and "there is none to undo" in again.json()["detail"]
    # The plan they put back gets no new fix: it is theirs now.
    goal = client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()
    assert _start(goal, back["plan"], tasks, events, Assessor()) is None


def test_a_fix_to_a_plan_the_operator_edited_is_left_behind(world):
    client, events, tasks = world
    goal, plan = _planned(client, events, tasks)
    task = _start(goal, plan, tasks, events, Assessor())
    edited = client.put(
        f"/v1/goals/{goal['id']}/plan", json={"drafts": [draft("store"), draft("api")]}, headers=OPERATOR
    )
    assert edited.status_code == 200, edited.text
    _end(events, tasks, task, [draft("api", ("store",))], [OPEN])
    goal = client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()

    collected = asyncio.run(suggestions.collect(goal, tasks, events))

    assert collected["status"] == "open", "it is kept as drafted, and does not replace what the operator made"
    shown = client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()
    assert shown["plan"]["version"] == 2 and shown["plan"]["source"] == "edited" and shown["applied_fix"] is None


def _status(goal_id: str, version: int) -> str:
    with db.connect(autocommit=True) as conn:
        row = conn.execute(
            "SELECT status FROM planner.plan_suggestion WHERE goal_id = %s AND for_version = %s", (goal_id, version)
        ).fetchone()
    return row[0]


def _dismiss_gap(client, goal_id: str, version: int, gap: str):
    return client.post(
        f"/v1/goals/{goal_id}/plan/gaps/dismiss", headers=OPERATOR, json={"version": version, "gap": gap}
    )


def test_dismissing_another_gap_keeps_the_fix_and_accepting_it_leaves_the_dismissed_gap_out(world, waiting):
    """The fix settles the clash. Dismissing the other question used to throw the fix away without a word."""
    client, events, tasks = world
    goal, plan = _planned(client, events, tasks)
    task = _start(goal, plan, tasks, events, Assessor())
    _end(events, tasks, task, [draft("api", ("store",))], [OPEN])
    asyncio.run(suggestions.collect(goal, tasks, events))

    dismissed = _dismiss_gap(client, goal["id"], 1, OPEN)

    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["suggestion"] is not None and _status(goal["id"], 1) == "open"
    assert "plan_fix_dismissed" not in events.types()
    accepted = client.post(f"/v1/goals/{goal['id']}/suggestion/accept", headers=OPERATOR)
    assert accepted.status_code == 200, accepted.text
    shown = client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()
    assert shown["plan"]["version"] == 2 and shown["plan"]["gaps"] == [], "the fix read the question before it went"


def test_dismissing_the_gap_a_fix_is_for_leaves_the_fix_behind_on_the_record(world, waiting):
    client, events, tasks = world
    goal, plan = _planned(client, events, tasks)
    task = _start(goal, plan, tasks, events, Assessor())
    _end(events, tasks, task, [draft("api", ("store",))], [OPEN])
    asyncio.run(suggestions.collect(goal, tasks, events))
    assert _status(goal["id"], 1) == "open"

    dismissed = _dismiss_gap(client, goal["id"], 1, CLASH)

    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["suggestion"] is None and _status(goal["id"], 1) == "dismissed"
    assert events.types().count("plan_fix_dismissed") == 1, "said in the record, as dismissing it by hand is"
    refused = client.post(f"/v1/goals/{goal['id']}/suggestion/accept", headers=OPERATOR)
    assert refused.status_code == 409
    assert "plan_fix_accepted" not in events.types()


def test_a_fix_still_drafting_at_a_dismissal_waits_and_is_accepted_without_the_dismissed_gap(world):
    """It becomes open later, holding the old gaps: it does not apply itself, and it can still be accepted."""
    client, events, tasks = world
    goal, plan = _planned(client, events, tasks)
    task = _start(goal, plan, tasks, events, Assessor())
    dismissed = _dismiss_gap(client, goal["id"], 1, OPEN)
    assert dismissed.status_code == 200 and _status(goal["id"], 1) == "drafting"
    _end(events, tasks, task, [draft("api", ("store",))], [OPEN])
    goal = client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()

    collected = asyncio.run(suggestions.collect(goal, tasks, events))

    assert collected["status"] == "open", "a version with a dismissed gap is no longer the planner's own"
    accepted = client.post(f"/v1/goals/{goal['id']}/suggestion/accept", headers=OPERATOR)
    assert accepted.status_code == 200, accepted.text
    shown = client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()
    assert (shown["plan"]["version"], shown["plan"]["gaps"]) == (2, [])
