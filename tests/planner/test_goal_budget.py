"""A goal's budget warns or blocks, with an allowed overage, and a blocked goal resumes in one step.

The math (budget.py) runs alone. The rest runs the planner's API against Postgres with fakes for the tracker,
the Task API and the event service: a goal's spend is the `attempt_finished` events the test records.
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from meta_harness.planner import budget
from meta_harness.planner.service import create_app

from .test_planner import OPERATOR, Events, FakeTasks, FakeTracker, _no_title, migrated, registry  # noqa: F401

REPO = "davecthomas/meta-harness"
CHECKS = "## Acceptance checks\n\n- make test"


def test_a_block_goal_stops_at_its_budget_plus_its_overage_and_a_warn_goal_never():
    assert budget.block_at("10", "block", 10) == Decimal("11.00")
    assert budget.block_at("10", "block", 0) == Decimal("10.00")
    assert budget.block_at("10", "warn", 10) is None and budget.block_at(None, "block", 10) is None

    assert budget.state("10", "block", 10, "9.99") == "ok"
    assert budget.state("10", "block", 10, "10") == "over"
    assert budget.state("10", "block", 10, "10.99") == "over"
    assert budget.state("10", "block", 10, "11") == "blocked"
    assert budget.state("10", "block", 0, "10") == "blocked"
    assert budget.state("10", "warn", 10, "500") == "over"
    assert budget.state(None, "block", 10, "500") == "ok"
    assert budget.state("10", "block", 10, "3", status="budget_exhausted") == "blocked"


def test_a_resume_budget_is_the_spend_plus_what_the_issues_left_are_likely_to_cost():
    worked = budget.resume_to(Decimal("26.79"), [Decimal("4"), Decimal("5.50")], 3, Decimal("10"))
    # 26.79 + 3 x 4.75 = 41.04, rounded up.
    assert worked["to"] == "42" and worked["each_usd"] == "4.75" and "3 issues not yet done" in worked["how"]
    none_finished = budget.resume_to(Decimal("2"), [], 2, Decimal("10"))
    assert none_finished["to"] == "22" and "default task limit" in none_finished["how"]
    assert budget.resume_to(Decimal("27"), [Decimal("3")], 0, Decimal("10"))["to"] == "28"


def test_the_issues_left_and_the_finished_tasks_are_read_from_the_goals_tasks_and_links():
    tasks = [
        {"id": "t-plan", "status": "closed", "task_type": "plan"},
        {"id": "t-done", "status": "closed", "task_type": "bug"},
        {"id": "t-ready", "status": "ready", "task_type": "bug"},
        {"id": "t-run", "status": "running", "task_type": "bug"},
        {"id": "t-gone", "status": "canceled", "task_type": "bug"},
    ]
    costs = {"t-plan": Decimal("9"), "t-done": Decimal("2"), "t-ready": Decimal("4"), "t-run": Decimal("1")}
    links = [
        {"state": "closed", "task_id": "t-done"},
        {"state": "open", "task_id": "t-ready"},
        {"state": "open", "task_id": "t-run"},
        {"state": "open", "task_id": "t-gone"},
        {"state": "open", "task_id": None},
    ]
    worked = budget.worked_out(Decimal("16"), tasks, costs, links, Decimal("10"))
    # Two issues left (the running one and the one with no task), at the two finished tasks' average of $3.
    assert worked["issues_left"] == 2 and worked["each_usd"] == "3.00" and worked["to"] == "22"


class Tasks(FakeTasks):
    """The Task API fake, taking the answer to a budget hold too."""

    def __init__(self):
        super().__init__()
        self.answered: list[tuple[str, str, str | None]] = []

    def budget(self, task_id, action, max_usd=None, actor=None):
        self.answered.append((task_id, action, actor))
        return self.tasks[task_id]


@pytest.fixture
def world():
    events, tracker, tasks = Events(), FakeTracker(), Tasks()
    app = create_app(
        events, tracker, tasks, operator_keys={"op-key": "dave"}, service_keys={}, sweep_seconds=0, titler=_no_title
    )
    with TestClient(app) as client:
        yield client, events, tracker, tasks


def _goal(client, tracker, issues: int = 2, **fields) -> dict:
    """An active goal with a $10 budget and this many open issues, each with a task after its first sweep."""
    import uuid

    label = f"budget-{uuid.uuid4().hex[:8]}"
    base = uuid.uuid4().int % 10**12
    for n in range(issues):
        tracker.add(REPO, base + n, body=CHECKS, labels=(label,))
    body = {"statement": "Fix things", "repos": [REPO], "label": label, "max_usd": "10", **fields}
    created = client.post("/v1/goals", json=body, headers=OPERATOR)
    assert created.status_code == 201, created.text
    _sweep(client, created.json())
    return _read(client, created.json())


def _sweep(client, goal: dict) -> None:
    swept = client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR)
    assert swept.status_code == 200 and swept.json()["errors"] == [], swept.text


def _read(client, goal: dict) -> dict:
    return client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()


def _spend(events, tasks, goal: dict, usd: str, status: str | None = None) -> dict:
    """One of the goal's tasks that has spent nothing yet spends this."""
    spent = {e.get("task_id") for e in events.stored.values() if e["event_type"] == "attempt_finished"}
    task = next(t for t in tasks.tasks.values() if t.get("goal_id") == goal["id"] and t["id"] not in spent)
    events.emit("attempt_finished", {"spent_usd": usd}, task_id=task["id"])
    if status:
        task["status"] = status
    return task


def test_a_goal_blocks_by_default_with_ten_percent_overage(world):
    client, _, tracker, _ = world
    goal = _goal(client, tracker)
    assert goal["budget_mode"] == "block" and goal["budget_overage_percent"] == 10
    assert Decimal(goal["budget_block_at"]) == Decimal("11") and goal["budget_state"] == "ok"
    assert goal["budget_resume_to"] is None

    warn = client.patch(f"/v1/goals/{goal['id']}", json={"budget_mode": "warn"}, headers=OPERATOR).json()
    assert warn["budget_mode"] == "warn" and warn["budget_block_at"] is None
    exact = {"budget_mode": "block", "budget_overage_percent": 0}
    assert (
        Decimal(client.patch(f"/v1/goals/{goal['id']}", json=exact, headers=OPERATOR).json()["budget_block_at"]) == 10
    )
    for bad in ({"budget_mode": "stop"}, {"budget_overage_percent": -1}):
        assert client.patch(f"/v1/goals/{goal['id']}", json=bad, headers=OPERATOR).status_code == 422


def test_reaching_the_budget_is_recorded_once_and_the_goal_runs_on_to_its_blocking_line(world):
    client, events, tracker, tasks = world
    goal = _goal(client, tracker, issues=3)
    _spend(events, tasks, goal, "10.40", status="closed")
    _sweep(client, goal)
    _sweep(client, goal)

    over = _read(client, goal)
    assert over["status"] == "active" and over["budget_state"] == "over"
    assert Decimal(over["spent_usd"]) == Decimal("10.40")
    reached = [p for t, p in events.events if t == "goal_budget_reached"]
    assert len(reached) == 1, "once per budget, however many sweeps see it"
    assert reached[0] == {
        "goal_id": goal["id"],
        "spent_usd": "10.40",
        "max_usd": "10.0000",
        "mode": "block",
        "block_at": "11.00",
    }
    # Two issues left (the third's task is done) at the one finished task's $10.40: 10.40 + 20.80, rounded up.
    assert Decimal(over["budget_resume_to"]) == Decimal("32")

    _spend(events, tasks, goal, "0.60")
    _sweep(client, goal)
    blocked = _read(client, goal)
    assert blocked["status"] == "budget_exhausted" and blocked["budget_state"] == "blocked"
    changes = [p["changes"] for t, p in events.events if t == "goal_changed"]
    assert {"status": {"from": "active", "to": "budget_exhausted"}} in changes


def test_a_warn_goal_records_the_budget_reached_and_never_stops(world):
    client, events, tracker, tasks = world
    goal = _goal(client, tracker, budget_mode="warn")
    _spend(events, tasks, goal, "250")
    _sweep(client, goal)

    shown = _read(client, goal)
    assert shown["status"] == "active" and shown["budget_state"] == "over" and shown["budget_block_at"] is None
    assert shown["budget_resume_to"] is None, "a warn goal never stopped: there is nothing to resume"
    [reached] = [p for t, p in events.events if t == "goal_budget_reached"]
    assert reached["mode"] == "warn" and reached["block_at"] is None
    refused = client.post(f"/v1/goals/{goal['id']}/resume-budget", headers=OPERATOR)
    assert refused.status_code == 409 and "only warns" in refused.json()["detail"]


def test_a_blocked_goal_goes_on_once_its_budget_is_raised_and_the_warning_can_be_recorded_again(world):
    client, events, tracker, tasks = world
    goal = _goal(client, tracker)
    _spend(events, tasks, goal, "12")
    _sweep(client, goal)
    assert _read(client, goal)["status"] == "budget_exhausted"

    client.patch(f"/v1/goals/{goal['id']}", json={"max_usd": "20"}, headers=OPERATOR)
    _sweep(client, goal)
    raised = _read(client, goal)
    assert raised["status"] == "active" and raised["budget_state"] == "ok"

    _spend(events, tasks, goal, "9")
    _sweep(client, goal)
    assert [p["max_usd"] for t, p in events.events if t == "goal_budget_reached"] == ["10.0000", "20.0000"]


def test_resume_raises_the_budget_by_a_worked_out_amount_and_tells_the_held_tasks(world):
    client, events, tracker, tasks = world
    goal = _goal(client, tracker, issues=3)
    _spend(events, tasks, goal, "6", status="closed")
    held = _spend(events, tasks, goal, "6", status="running")
    held |= {"waiting_on": "budget", "status_reason": "fix run 2 needs about $1. Raise the goal's budget, or end"}
    own_limit = next(t for t in tasks.tasks.values() if t.get("goal_id") == goal["id"] and t["status"] == "queued")
    own_limit |= {"status": "running", "waiting_on": "budget", "status_reason": "Raise the task's spend limit"}
    _sweep(client, goal)
    assert _read(client, goal)["status"] == "budget_exhausted"

    resumed = client.post(f"/v1/goals/{goal['id']}/resume-budget", headers=OPERATOR)

    assert resumed.status_code == 200, resumed.text
    answer = resumed.json()
    # $12 spent, plus the two issues not yet done at the one finished task's $6.
    assert Decimal(answer["from"]) == Decimal("10") and answer["to"] == "24" and "$6.00 each" in answer["how"]
    assert answer["goal"]["status"] == "active" and Decimal(answer["goal"]["max_usd"]) == Decimal("24")
    assert answer["goal"]["budget_state"] == "ok" and answer["goal"]["budget_resume_to"] is None
    assert answer["tasks_told"] == [held["id"]] and tasks.answered == [(held["id"], "raise", "dave")]
    changed = [p for t, p in events.events if t == "goal_changed" and p.get("resumed")]
    assert len(changed) == 1 and set(changed[0]["changes"]) == {"max_usd", "status"}

    again = client.post(f"/v1/goals/{goal['id']}/resume-budget", headers=OPERATOR)
    assert again.status_code == 409 and "nothing to resume" in again.json()["detail"]


def test_raising_the_budget_by_hand_takes_effect_at_once(world):
    client, events, tracker, tasks = world
    goal = _goal(client, tracker, issues=3)
    held = _spend(events, tasks, goal, "26.79", status="running")
    held |= {"waiting_on": "budget", "status_reason": "fix run 2 needs about $1. Raise the goal's budget, or end"}
    _sweep(client, goal)
    assert _read(client, goal)["budget_resume_to"] is not None

    raised = client.patch(f"/v1/goals/{goal['id']}", json={"max_usd": "30"}, headers=OPERATOR).json()

    # No sweep between: the answer is already the new budget's.
    assert raised["status"] == "active" and raised["budget_state"] == "ok"
    assert raised["budget_resume_to"] is None, "a goal within its budget has nothing to resume"
    assert tasks.answered == [(held["id"], "raise", "dave")]
    refused = client.post(f"/v1/goals/{goal['id']}/resume-budget", headers=OPERATOR)
    assert refused.status_code == 409 and "nothing to resume" in refused.json()["detail"]


def test_resume_takes_an_amount_and_refuses_one_that_leaves_the_goal_stopped(world):
    client, events, tracker, tasks = world
    goal = _goal(client, tracker)
    _spend(events, tasks, goal, "26.79")
    _sweep(client, goal)

    short = client.post(f"/v1/goals/{goal['id']}/resume-budget", json={"add_usd": "5"}, headers=OPERATOR)
    assert short.status_code == 409 and "stops the goal at $16.50" in short.json()["detail"]
    enough = client.post(f"/v1/goals/{goal['id']}/resume-budget", json={"add_usd": "20"}, headers=OPERATOR)
    assert enough.status_code == 200 and Decimal(enough.json()["to"]) == Decimal("30")
    assert enough.json()["goal"]["status"] == "active"

    no_budget = _goal(client, tracker, max_usd=None)
    refused = client.post(f"/v1/goals/{no_budget['id']}/resume-budget", headers=OPERATOR)
    assert refused.status_code == 409 and "no budget" in refused.json()["detail"]


def test_under_the_track_budget_policy_no_goal_stops_and_spend_is_still_recorded(world, monkeypatch):
    from meta_harness.config import store as config_store

    client, events, tracker, tasks = world
    goal = _goal(client, tracker)
    _spend(events, tasks, goal, "12")
    _sweep(client, goal)
    assert _read(client, goal)["status"] == "budget_exhausted"

    monkeypatch.setattr(config_store, "budget_policy", lambda fresh=False: "track")
    tracked = _read(client, goal)
    # Its own mode is still block; the policy above it is what lets it go on.
    assert (tracked["budget_policy"], tracked["budget_mode"]) == ("track", "block")
    assert tracked["budget_block_at"] is None and tracked["budget_state"] == "over"
    assert tracked["budget_resume_to"] is None
    refused = client.post(f"/v1/goals/{goal['id']}/resume-budget", headers=OPERATOR)
    assert refused.status_code == 409 and "only tracks spend" in refused.json()["detail"]

    _spend(events, tasks, goal, "30")
    _sweep(client, goal)
    after = _read(client, goal)
    assert after["status"] == "active", "its next sweep makes a goal the policy no longer stops active"
    assert Decimal(after["spent_usd"]) == Decimal("42"), "the spend is still added up"

    monkeypatch.setattr(config_store, "budget_policy", lambda fresh=False: "block")
    _sweep(client, goal)
    assert _read(client, goal)["status"] == "budget_exhausted", "and blocking again stops it again"
