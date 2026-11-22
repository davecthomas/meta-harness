"""A task that runs short of budget holds and asks, instead of failing in the middle of its work.

The RunTask workflow runs against the review loop's scripted activities, with stand-ins for the task service,
the goal's budget and the hold. No model, gateway, GitHub or planner is called.
"""

import asyncio
from dataclasses import replace
from decimal import Decimal

from temporalio import activity
from temporalio.activity import _Definition
from temporalio.worker import Worker

from meta_harness.run_service.budget_activities import BudgetHold, GoalBudget
from meta_harness.run_service.shared import HarnessRequest, OperatorMessage, RunTaskInput, TaskSpec
from meta_harness.run_service.workflows import RunTask

from .test_replay import _task_activities
from .test_review_loop import CHANGES, CLEAN, QUEUE, REPO, Script, env  # noqa: F401 - env is a fixture


class World:
    """The stand-ins a held task talks to: its spend limit, its goal's budget as each run finds it, and each
    hold the workflow set or ended."""

    def __init__(self, script: Script, limit: str = "10.00", goal_id: str = "", budgets: list[GoalBudget] = ()):
        self.script = script
        self.limit = limit
        self.goal_id = goal_id
        self.budgets = list(budgets)
        self.holds: list[BudgetHold] = []
        self.held = asyncio.Event()
        # The kinds of run, in order, that end on their spend limit instead of finishing.
        self.cut_short: list[str] = []

    def activities(self) -> list:
        world = self
        base = [*self.script.activities(), *_task_activities(True, self.goal_id)]
        original = next(a for a in base if _Definition.from_callable(a).name == "run_harness")

        @activity.defn(name="load_task")
        async def load_task(task_id: str) -> TaskSpec:
            return TaskSpec(
                task_id=task_id,
                status="queued",
                repo=REPO,
                title="Fix the bug",
                description="The bug is in app.py.",
                checks=["pytest -q"],
                pin="",
                floor="",
                ceiling="",
                spend_limit_usd=world.limit,
                closes="owner/repo#1",
                goal_id=world.goal_id,
            )

        @activity.defn(name="goal_budget")
        async def goal_budget(goal_id: str) -> GoalBudget:
            return world.budgets.pop(0) if len(world.budgets) > 1 else (world.budgets or [GoalBudget()])[0]

        @activity.defn(name="budget_hold")
        async def budget_hold(hold: BudgetHold) -> None:
            world.holds.append(hold)
            if hold.waiting:
                world.held.set()

        @activity.defn(name="run_harness")
        async def run_harness(request: HarnessRequest):
            result = await original(request)
            if world.cut_short and world.cut_short[0] == request.prompt:
                world.cut_short.pop(0)
                if result.review is not None:
                    world.script.verdicts.insert(0, result.review)
                return replace(result, status="ERROR", end_reason="spend_limit", error="402 from the gateway")
            return result

        replaced = {"load_task", "goal_budget", "budget_hold", "run_harness"}
        kept = [a for a in base if _Definition.from_callable(a).name not in replaced]
        return [*kept, load_task, goal_budget, budget_hold, run_harness]


async def _start(env, world: World, name: str):  # noqa: F811 - env is the fixture's name
    worker = Worker(env.client, task_queue=QUEUE, workflows=[RunTask], activities=world.activities())
    await worker.__aenter__()
    handle = await env.client.start_workflow(
        RunTask.run, RunTaskInput(task="", repo="", task_id=f"t-{name}"), id=f"run-task-t-{name}", task_queue=QUEUE
    )
    return worker, handle


async def _answer(handle, world: World, text: str) -> None:
    await asyncio.wait_for(world.held.wait(), timeout=30)
    await handle.signal(RunTask.operator_message, OperatorMessage(message_id=1, text=text, when="budget"))


async def test_a_task_short_of_its_limit_holds_and_carries_on_when_the_limit_is_raised(env):  # noqa: F811
    # Each run costs $1.00. After the first run, a review and a fix, $3.00 of $2.50 is spent: the second review
    # needs about $1.00, so the task holds for it.
    world = World(Script(verdicts=[CHANGES, CLEAN], fix_commits=["c1"]), limit="2.50")
    worker, handle = await _start(env, world, "raise")
    try:
        await _answer(handle, world, "raise:6.00")
        result = await handle.result()
    finally:
        await worker.__aexit__(None, None, None)

    assert result.outcome == "clean", "it went on to the review it was held before"
    held, resumed = world.holds
    assert held.waiting and "review run 4 needs about $1.00" in held.reason
    assert "the task has $-0.50 left of its $2.50 spend limit" in held.reason
    assert "Raise the task's spend limit, or end the task" in held.reason
    assert not resumed.waiting and "the budget was raised; carrying on with review run 4" in resumed.reason
    assert Decimal(world.script.runs[-1].spend_limit_usd) == Decimal("3.00"), "the raised limit less what is spent"


async def test_a_raise_that_is_not_enough_holds_the_task_again_with_what_is_short_now(env):  # noqa: F811
    import os

    from .test_replay import HISTORIES, _public

    # As above, $3.00 of $2.50 spent before the second review: $3.50 leaves $0.50 of the $1.00 it needs.
    world = World(Script(verdicts=[CHANGES, CLEAN], fix_commits=["c1"]), limit="2.50")
    worker, handle = await _start(env, world, "raise-short")
    try:
        await _answer(handle, world, "raise:3.50")
        for _ in range(600):
            if len(world.holds) > 1:
                break
            await asyncio.sleep(0.05)
        await handle.signal(RunTask.operator_message, OperatorMessage(message_id=2, text="raise:6.00", when="budget"))
        result = await handle.result()
        history = await handle.fetch_history()
    finally:
        await worker.__aexit__(None, None, None)

    assert result.outcome == "clean"
    first, again, resumed = world.holds
    assert first.waiting and "the task has $-0.50 left of its $2.50 spend limit" in first.reason
    assert again.waiting and "review run 4 needs about $1.00" in again.reason
    assert "the task has $0.50 left of its $3.50 spend limit" in again.reason, "the reason as the raise left it"
    assert not resumed.waiting and "carrying on with review run 4" in resumed.reason
    # The path behind the budget-hold-again patch, recorded for the replay test (its docstring says how).
    path = HISTORIES / "patched-budget-hold-again.json"
    if os.environ.get("MH_RECORD_HISTORIES") and not path.exists():
        path.write_text(_public(history.to_json()))


async def test_a_held_task_an_operator_ends_ends_as_it_would_have(env):  # noqa: F811
    world = World(Script(verdicts=[CHANGES, CLEAN], fix_commits=["c1"]), limit="2.50")
    worker, handle = await _start(env, world, "end")
    try:
        await _answer(handle, world, "end")
        result = await handle.result()
    finally:
        await worker.__aexit__(None, None, None)

    assert result.outcome == "review_unresolved"
    assert "spent its $2.50 spend limit" in result.outcome_reason
    assert len(world.script.runs) == 3, "no run started after the hold"
    assert not world.holds[-1].waiting and "an operator ended it" in world.holds[-1].reason


async def test_a_run_cut_short_by_its_limit_is_run_again_once_the_limit_is_raised(env):  # noqa: F811
    world = World(Script(verdicts=[CHANGES, CLEAN], fix_commits=["c1"]), limit="2.50")
    world.cut_short = ["fix"]
    worker, handle = await _start(env, world, "cut")
    try:
        await _answer(handle, world, "raise:9.00")
        result = await handle.result()
    finally:
        await worker.__aexit__(None, None, None)

    assert result.outcome == "clean"
    assert [r.prompt for r in world.script.runs].count("fix") == 2, "the cut-off fix ran again"
    assert world.holds[0].waiting and "fix run 4 needs about $1.00" in world.holds[0].reason


async def test_a_goals_running_tasks_share_what_the_goal_has_left(env):  # noqa: F811
    # $10 budget, $4 spent, 3 of its tasks running: each may spend $2 next, however much its own limit allows.
    world = World(Script(verdicts=[CLEAN]), goal_id="g-1", budgets=[GoalBudget("10", "4", 3)])
    worker, handle = await _start(env, world, "share")
    try:
        result = await handle.result()
    finally:
        await worker.__aexit__(None, None, None)

    assert result.outcome == "clean" and not world.holds
    assert Decimal(world.script.runs[0].spend_limit_usd) == Decimal("2.0000")


async def test_a_task_held_for_its_goals_budget_goes_on_when_the_goals_budget_is_raised(env):  # noqa: F811
    spent, raised = GoalBudget("10", "9.95", 2), GoalBudget("30", "9.95", 2)
    world = World(Script(verdicts=[CLEAN]), goal_id="g-1", budgets=[spent, spent, raised])
    worker, handle = await _start(env, world, "goal")
    try:
        result = await handle.result()  # no answer: the task looks at the goal's budget again by itself
    finally:
        await worker.__aexit__(None, None, None)

    assert result.outcome == "clean"
    held, resumed = world.holds
    assert held.waiting and "goal g-1 has spent $9.95 of its $10.00 budget" in held.reason
    assert "Raise the goal's budget, or end the task" in held.reason
    assert not resumed.waiting


def test_a_goal_with_no_budget_caps_nothing():
    assert GoalBudget().share() is None
    assert GoalBudget("10", "12", 2).share() == Decimal("-1.0000")


def test_what_a_goal_has_left_is_measured_to_its_blocking_line():
    # $10 budget with 10% overage: $10.50 spent leaves $0.50 before the line, shared by two running tasks.
    assert GoalBudget("10", "10.50", 2, block_at="11.00").share() == Decimal("0.2500")


def test_a_share_is_never_more_than_is_left_after_the_other_tasks_grants_or_than_the_task_could_spend():
    # $20 left, two running: an equal share is $10, but $14 is granted to the other task's run under way.
    assert GoalBudget("20", "0", 2, granted_usd="14").share() == Decimal("6.0000")
    assert GoalBudget("20", "0", 2, granted_usd="4").share() == Decimal("10.0000")
    # A task holds back only what its own spend limit lets it spend.
    assert GoalBudget("20", "0", 1, task_limit_usd="8.00").share() == Decimal("8.0000")
    assert GoalBudget("20", "0", 1, granted_usd="20", task_limit_usd="8.00").share() == Decimal("0.0000")


def test_tasks_asking_one_after_another_are_never_granted_more_than_the_goal_has_left(monkeypatch, conn):
    """Spend is recorded when a run ends. Before grants were written down, a task asking alone was granted
    everything left, and the next ones a half and a third of the same money."""
    from meta_harness import migrations
    from meta_harness.events import client as events_client
    from meta_harness.planner import client as planner_client
    from meta_harness.run_service import budget_activities, goal_grants

    migrations.apply(conn)
    goal_id = f"g-{id(conn)}"
    names = [f"{goal_id}-a", f"{goal_id}-b", f"{goal_id}-c"]
    running: list[str] = []
    spent: dict[str, str] = {}

    class Goals:
        def __init__(self, **kwargs):
            pass

        def get(self, goal_id):
            return {"max_usd": "20.0000", "budget_mode": "block", "budget_block_at": "20.00"}

    class Tasks:
        def list(self, statuses, goal_id, limit):
            return [{"id": t, "status": "running" if t in running else "ready", "max_usd": "50"} for t in names]

    class Events:
        def __init__(self, **kwargs):
            pass

        def read(self, from_position, limit, task_id=None, event_type=None):
            return {"events": [{"payload": {"spent_usd": spent[task_id]}}] if task_id in spent else []}

    monkeypatch.setattr(planner_client, "GoalsClient", Goals)
    monkeypatch.setattr(events_client, "EventClient", Events)
    monkeypatch.setattr(budget_activities, "_tasks", lambda: Tasks())
    monkeypatch.setattr(budget_activities, "_spend_limit", lambda task: "50.00")

    shares = []
    for name in names:
        running.append(name)
        shares.append(budget_activities._goal_budget(goal_id, name).share())

    assert shares == [Decimal("20.0000"), Decimal("0.0000"), Decimal("0.0000")]
    assert sum(shares) <= Decimal("20")
    assert goal_grants.outstanding(conn, goal_id, names) == Decimal("20")

    # The first run ends having spent $3: its grant is given back, and the next to ask gets a share of the rest.
    spent[names[0]] = "3.00"
    goal_grants.release(names[0])
    running.remove(names[0])
    again = budget_activities._goal_budget(goal_id, names[1])
    assert again.share() == Decimal("8.5000"), "$17 left, shared by the two still running"
    last = budget_activities._goal_budget(goal_id, names[2])
    assert last.granted_usd == "8.5000" and last.share() == Decimal("8.5000")
    assert Decimal("3") + again.share() + last.share() <= Decimal("20")

    # Asking again replaces a task's own grant: it is never counted against itself.
    assert budget_activities._goal_budget(goal_id, names[1]).share() == Decimal("8.5000")
    for name in names:
        goal_grants.release(name)


def test_a_grant_counts_only_for_running_tasks(conn):
    from meta_harness import migrations
    from meta_harness.run_service import goal_grants

    migrations.apply(conn)
    goal_id, task = f"g-{id(conn)}-r", f"t-{id(conn)}-r"
    goal_grants.reserve(conn, task, goal_id, Decimal("5"))
    assert goal_grants.outstanding(conn, goal_id, [task]) == Decimal("5")
    assert goal_grants.outstanding(conn, goal_id, []) == 0, "a task no longer running holds nothing back"
    goal_grants.reserve(conn, task, goal_id, Decimal("0"))
    assert goal_grants.outstanding(conn, goal_id, [task]) == 0


def test_the_hold_says_when_money_is_held_for_runs_under_way_and_is_still_read_back():
    from meta_harness import budget_request

    why = budget_request.goal_why("g-1", "5", "20", "0", 2, "15")
    assert why.endswith("leaves $0.00 for each of its 2 running tasks. $15.00 is held for runs under way")
    asked = budget_request.parse(budget_request.hold_reason("run 2", "3", why))
    assert (asked["for"], asked["goal_id"], asked["running"]) == ("goal", "g-1", 2)
    plain = budget_request.goal_why("g-1", "5", "20", "7.5", 2)
    assert plain.endswith("for each of its 2 running tasks") and budget_request.parse(
        budget_request.hold_reason("run 2", "3", plain)
    )


def test_the_goals_budget_is_read_with_its_blocking_line_and_a_warn_goal_has_none(monkeypatch):
    from meta_harness.events import client as events_client
    from meta_harness.planner import client as planner_client
    from meta_harness.run_service import budget_activities

    goal = {"max_usd": "10.0000", "budget_mode": "block", "budget_block_at": "11.00"}

    class Goals:
        def __init__(self, **kwargs):
            pass

        def get(self, goal_id):
            return goal

    class Tasks:
        def list(self, statuses, goal_id, limit):
            return [{"id": "t-1", "status": "running"}, {"id": "t-2", "status": "closed"}]

    class Events:
        def __init__(self, **kwargs):
            pass

        def read(self, from_position, limit, task_id=None, event_type=None):
            return {"events": [{"payload": {"spent_usd": "5.25"}}]}

    monkeypatch.setattr(planner_client, "GoalsClient", Goals)
    monkeypatch.setattr(events_client, "EventClient", Events)
    monkeypatch.setattr(budget_activities, "_tasks", lambda: Tasks())

    found = budget_activities._goal_budget("g-1")
    assert (found.max_usd, found.spent_usd, found.running, found.block_at) == ("10.0000", "10.50", 1, "11.00")
    assert found.share() == Decimal("0.5000")

    goal["budget_mode"], goal["budget_block_at"] = "warn", None
    assert budget_activities._goal_budget("g-1") == GoalBudget(), "a goal that only warns caps no run"
