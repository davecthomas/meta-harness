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
    assert held.waiting and "goal g-1 has spent $9.95 of its $10 budget" in held.reason
    assert "Raise the goal's budget, or end the task" in held.reason
    assert not resumed.waiting


def test_a_goal_with_no_budget_caps_nothing():
    assert GoalBudget().share() is None
    assert GoalBudget("10", "12", 2).share() == Decimal("-1.0000")
