"""Budget holds: what a task's goal has left to spend, and marking a task as waiting on an operator for budget.

A task's spend limit and its goal's budget used to be checked only before work started: a run that reached the
limit failed in the middle of its work, and a goal's tasks, each started while the goal still had budget, could
together spend several times it. The RunTask workflow now asks before every run, behind its `budget-hold`
patch. When the task or the goal has too little left for the run, the task waits, still running, marked
`waiting_on: budget`, until an operator raises the limit or ends it.
"""

import asyncio
import os
from dataclasses import dataclass
from decimal import Decimal

from temporalio import activity
from temporalio.exceptions import ApplicationError

from meta_harness.run_service.task_activities import _task_api_error, _tasks
from meta_harness.tasks.client import TaskApiError


@dataclass
class GoalBudget:
    """A goal's budget as a run finds it. max_usd is "" when the goal has none, so nothing is held for it."""

    max_usd: str = ""
    spent_usd: str = "0"
    # The goal's tasks that are running now, this one included: what is left is shared among them.
    running: int = 1

    def share(self) -> Decimal | None:
        """What one of the goal's running tasks may spend next: an equal share of what the goal has left, so
        tasks running side by side cannot together pass its budget. None when the goal has no budget."""
        if not self.max_usd:
            return None
        left = Decimal(self.max_usd) - Decimal(self.spent_usd)
        return (left / max(self.running, 1)).quantize(Decimal("0.0001"))


@dataclass
class BudgetHold:
    task_id: str
    # Why the task waits, shown as its status reason; "" ends the wait, and reason says what happens next.
    waiting: bool
    reason: str


def _goal_budget(goal_id: str) -> GoalBudget:
    from meta_harness.events.client import EventClient
    from meta_harness.planner.client import GoalsClient

    key = os.environ.get("MH_SERVICE_KEY", "")
    goal = GoalsClient(api_key=key).get(goal_id)
    if goal.get("max_usd") is None:
        return GoalBudget()
    events = EventClient(source="run_service")
    tasks = _tasks().list(None, goal_id, 1000)
    spent = Decimal(0)
    for task in tasks:
        page = events.read(0, 5000, task_id=task["id"], event_type="attempt_finished")
        spent += sum((Decimal(e["payload"]["spent_usd"]) for e in page["events"]), Decimal(0))
    running = sum(1 for task in tasks if task["status"] == "running")
    return GoalBudget(max_usd=str(goal["max_usd"]), spent_usd=str(spent), running=max(running, 1))


@activity.defn
async def goal_budget(goal_id: str) -> GoalBudget:
    """The goal's budget, what its tasks' runs have spent, and how many of its tasks are running."""
    try:
        return await asyncio.to_thread(_goal_budget, goal_id)
    except TaskApiError as err:
        raise _task_api_error(err) from err
    except Exception as err:  # noqa: BLE001 - an unreachable planner or event service is retried
        raise ApplicationError(f"could not read goal {goal_id}'s budget: {err}", type="BudgetUnavailable") from err


@activity.defn
async def budget_hold(hold: BudgetHold) -> None:
    """Marks the running task as waiting on an operator for budget, or as waiting no longer, with why."""
    waiting_on = "budget" if hold.waiting else ""
    try:
        await asyncio.to_thread(
            lambda: _tasks().set_status(hold.task_id, "running", hold.reason, waiting_on=waiting_on)
        )
    except TaskApiError as err:
        raise _task_api_error(err) from err
