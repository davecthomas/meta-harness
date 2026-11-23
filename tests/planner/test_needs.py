"""What needs the operator is decided in one place, and every page reads that one answer."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meta_harness.planner import actions
from meta_harness.supervisor.goals import goal_standing

from .test_planner import OPERATOR, Events, FakeTasks, _no_title, migrated, registry  # noqa: F401
from .test_planning import WritingTracker, _feature, _planned, draft

ROOT = Path(__file__).resolve().parents[2]
PR = "https://github.com/o/r/pull/7"


@pytest.fixture
def world():
    from meta_harness.planner.service import create_app

    events, tasks = Events(), FakeTasks()
    app = create_app(
        events, WritingTracker(), tasks, operator_keys={"op-key": "dave"}, service_keys={}, sweep_seconds=0,
        titler=_no_title,
    )  # fmt: skip
    with TestClient(app) as client:
        yield client, events, tasks


def _mine(client, goal_id: str) -> list[dict]:
    return [n for n in client.get("/v1/needs", headers=OPERATOR).json()["needs"] if n["goal_id"] == goal_id]


def test_a_plan_waiting_for_approval_needs_the_operator_everywhere_at_once(world):
    """The Overview's report said a plan waited for approval while the list under it, the tile and the goal's
    own badge said nothing needed anyone: each had its own rules."""
    client, events, tasks = world
    goal = _feature(client, statement="Add exports")
    assert _mine(client, goal["id"]) == [], "a plan still being drafted needs nobody"
    assert client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["standing"]["state"] == "held"

    _planned(client, events, tasks, goal, [draft("store"), draft("api", ("store",))])

    [need] = _mine(client, goal["id"])
    assert (need["action"], need["asks"], need["goal"]) == ("approve_plan", "Approve the plan", "Add exports")
    assert need["what"] == "The plan is ready, with 2 new tasks. Nothing runs until you approve it."
    assert need["key"] == f"approve_plan:{goal['id']}"
    # The goal's own badge, its panel and the Overview's report rows say the same, from the same list.
    standing = client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["standing"]
    assert (standing["state"], standing["reason"]) == ("needs_you", "its plan waits for you")
    panel = client.get(f"/v1/goals/{goal['id']}/updates", headers=OPERATOR).json()["needs"]
    assert [n["action"] for n in panel] == ["approve_plan"]

    assert client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR).status_code == 200
    assert not [n for n in _mine(client, goal["id"]) if n["action"] == "approve_plan"], "approved: asked no more"


def test_a_tasks_place_in_the_list_is_the_task_shepherds_word_not_its_status():
    failed = {"id": "t-1", "status": "failed", "pr_url": None}
    mine = {"owner": "operator", "situation": "failed", "reason": "it failed twice", "since": "2026-11-23T10:00:00Z"}
    its = {"owner": "supervisor", "situation": "retrying", "reason": "it retries", "since": "2026-11-23T10:00:00Z"}

    assert actions.waits_on_operator(failed), "not looked at yet: judged by its status"
    assert actions.waits_on_operator({**failed, "standing": mine})
    assert not actions.waits_on_operator({**failed, "standing": its}), "the Shepherd is about to retry it"
    assert not actions.waits_on_operator({**failed, "standing": {**mine, "situation": "goal_not_active"}})
    assert actions.waits_on_operator({"id": "t-2", "status": "running", "standing": mine}), "stuck, and the operator's"
    assert not actions.waits_on_operator({"id": "t-3", "status": "ready", "pr_url": PR}, auto_merge=True)


def test_each_need_says_what_it_asks_and_a_task_of_no_goal_is_listed_too():
    budget = {"id": "t-1", "title": "Export", "status": "running", "waiting_on": "budget", "external_ref": "mh:i-1"}
    asked = {**budget, "budget_request": {"for": "task", "add_usd": "2.5"}}
    review = {"id": "t-2", "title": "Import", "status": "running", "waiting_on": "review", "external_ref": "mh:i-2"}

    [limit] = actions.for_task(budget)
    assert (limit["action"], limit["asks"], limit["title"], limit["goal_id"]) == (
        "open_task",
        "Raise the spend limit",
        "Export",
        "",
    )
    assert actions.for_task(asked)[0]["asks"] == "Grant $2.50 more"
    assert actions.for_task(review)[0]["asks"] == "Decide on the review"
    assert actions.for_task({"id": "t-3", "title": "Done", "status": "closed", "waiting_on": "review"}) == []
    over = {"id": "g-1", "status": "active", "budget_state": "over", "max_usd": "40.00"}
    [past] = actions.for_goal(over, [], [])
    assert (past["action"], past["asks"]) == ("raise_budget", "Over budget")
    assert past["what"] == "The goal is past its budget of $40.00 and keeps running."
    assert set(actions.ASKS) >= {*actions.ACTIONS, *actions.STATE_ACTIONS} - {"open_task"} | {"open_task"}


def test_a_goal_being_planned_is_held_until_its_plan_waits_on_the_operator():
    assert goal_standing("planning", [])["state"] == "held"
    waiting = goal_standing("planning", [], plan_waits=True)
    assert (waiting["state"], waiting["ask"]) == (
        "needs_you",
        "Read the plan on its page, then approve it or ask for a new one.",
    )
    assert goal_standing("active", [], plan_waits=True)["state"] != "needs_you", "only a goal being planned"


def test_every_place_that_says_what_needs_the_operator_reads_the_one_list():
    """A new page or control that works it out for itself is how they came to disagree."""
    planner = ROOT / "src" / "meta_harness" / "planner"
    service, overview = (planner / "service.py").read_text(), (planner / "overview.py").read_text()
    # The goal's badge, the goal's panel, the report's facts and buttons, and the answers' buttons.
    assert "needs.plan_waits(needs.of_goal(" in service and "needs.by_id" in service
    assert overview.count("all_needs.") >= 2 and "all_needs.of_goal(goal, tasks, MAX_NEEDS)" in overview
    # needs.py decides through actions.py alone.
    assert "actions.for_goal(" in (planner / "needs.py").read_text()

    console = ROOT / "console" / "src"
    shared = (console / "features" / "overview" / "needsYou.ts").read_text()
    assert '"/v1/needs"' in shared
    # The console's copy decides nothing: no rule on a task's or a goal's status, standing or wait.
    rules = re.findall(r"\b(?:status|waiting_on|budget_state)\s*===|standing\??\.(?:owner|state)", shared)
    assert rules == [], f"needsYou.ts works something out for itself: {rules}"
    # Each page that lists or counts what needs the operator takes it from that copy.
    for page in ("features/overview/Overview.tsx", "components/Inbox.tsx", "features/overview/WorkReport.tsx"):
        assert "needsYou" in (console / page).read_text(), page
    readers = [p for p in console.rglob("*.ts*") if ".test." not in p.name and '"/v1/needs"' in p.read_text()]
    assert [p.name for p in readers] == ["needsYou.ts"], "one copy of the list, read in one place"
    assert "decided in one place" in (ROOT / "AGENTS.md").read_text()
