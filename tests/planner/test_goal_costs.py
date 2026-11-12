"""Issue #296: the goal list shows what each goal has cost, the same total as the goal's Spend section."""

from decimal import Decimal

from meta_harness.planner import goal_costs

from .test_planner import OPERATOR, REPO, _goal, _label, _number, _sweep, api, migrated, parts, registry  # noqa: F401


def _cost(events, task_id: str, usd: str) -> None:
    events.emit("attempt_finished", {"spent_usd": usd, "tier": "complex"}, task_id=task_id)


def test_each_goal_lists_what_its_tasks_runs_spent_the_same_as_its_spend_section(api, parts):  # noqa: F811 - fixtures imported above
    events, tracker, tasks = parts
    spent, idle = _goal(api, _label()), _goal(api, _label())
    for _ in range(2):
        tracker.add(REPO, _number(), labels=[spent["label"]])
    _sweep(api, spent)
    first, second = [t["id"] for t in tasks.tasks.values() if t["goal_id"] == spent["id"]]
    _cost(events, first, "1.25")
    _cost(events, first, "0.30")
    _cost(events, second, "2.00")
    _cost(events, "t-no-goal", "9.00")
    api.app.state.goal_costs = goal_costs.Cache(tasks, events)

    listed = {g["id"]: g for g in api.get("/v1/goals", headers=OPERATOR).json()["goals"]}

    assert Decimal(listed[spent["id"]]["cost_usd"]) == Decimal("3.55")
    assert listed[idle["id"]]["cost_usd"] == "0" and listed[idle["id"]]["cost_unknown"] is None
    page = api.get(f"/v1/goals/{spent['id']}/spend", headers=OPERATOR).json()
    assert Decimal(page["usd"]) == Decimal(listed[spent["id"]]["cost_usd"]), "the list and the goal's page agree"


def test_a_cost_that_cannot_be_read_is_unknown_with_why_never_zero(api, parts):  # noqa: F811 - fixtures imported above
    events, tracker, tasks = parts
    goal = _goal(api, _label())

    class Down:
        def read(self, **kwargs):
            raise ConnectionError("the event service did not answer")

    api.app.state.goal_costs = goal_costs.Cache(tasks, Down())
    listed = {g["id"]: g for g in api.get("/v1/goals", headers=OPERATOR).json()["goals"]}

    assert listed[goal["id"]]["cost_usd"] is None
    assert "did not answer" in listed[goal["id"]]["cost_unknown"]


def test_a_reading_is_kept_for_a_while_and_a_failed_one_is_not(parts):  # noqa: F811 - fixtures imported above
    events, tracker, tasks = parts
    reads = []

    class Counting:
        fail = True

        def read(self, **kwargs):
            reads.append(kwargs)
            if self.fail:
                raise ConnectionError("down")
            return {"events": []}

    counting = Counting()
    cache = goal_costs.Cache(tasks, counting)
    assert cache.get()[0] is None and cache.get()[0] is None and len(reads) == 2, "a failure is tried again"
    counting.fail = False
    assert cache.get() == ({}, "") and cache.get() == ({}, "") and len(reads) == 3, "a reading is kept"
