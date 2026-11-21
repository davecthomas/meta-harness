"""Raising a task's own spend limit once, within bounds, when it is held for it.

A run that cannot start for the task's spend limit holds the task and says how much it needs (budget_request).
The supervisor answers it itself when the raise is small and safe: it is for the task's own limit, never the
goal's budget; the new limit is at most twice the old one; and the goal's budget has room for the raise. Past
any of those, or once its tries are used, the hold is the operator's, as before. A goal's budget is never
raised by a remedy.
"""

import asyncio
from decimal import Decimal

from meta_harness import budget_request
from meta_harness.config.kinds import remedy_tries
from meta_harness.money import usd
from meta_harness.run_service import routing
from meta_harness.supervisor import ledger
from meta_harness.supervisor.remedies import Context, Outcome, Remedy

# The most a raise multiplies the task's spend limit by.
MOST = Decimal(2)


def raise_to(task: dict, goal: dict | None) -> Decimal | None:
    """The limit the supervisor may raise the held task to, or None when it may not: the hold is not for the
    task's own limit, the run needs more than twice it, or the goal's budget has no room for the raise."""
    request = budget_request.parse(task.get("status_reason"))
    if not request or request.get("for") != "task":
        return None
    limit, to = Decimal(request["limit_usd"]), Decimal(request["to_usd"])
    if limit <= 0 or to > limit * MOST:
        return None
    if goal and goal.get("max_usd") is not None:
        room = Decimal(str(goal["max_usd"])) - Decimal(str(goal.get("spent_usd") or 0))
        if room < to - limit:
            return None
    return to


class RaiseLimit(Remedy):
    id = "raise_limit"

    def of(self, task: dict) -> int | None:
        return remedy_tries(routing.task_policy(), self.id)

    def tries(self, task: dict) -> int:
        return ledger.tries(task["id"], self.id)

    async def apply(self, ctx: Context) -> Outcome:
        to = raise_to(ctx.task, ctx.goal)
        if to is None:
            raise ValueError("the raise this hold needs is more than the Task Shepherd may make")
        was = budget_request.parse(ctx.task.get("status_reason"))["limit_usd"]
        await asyncio.to_thread(ctx.supervisor.tasks.budget, ctx.task["id"], "raise", str(to), "supervisor")
        return Outcome(
            "started",
            f"raised its spend limit from {usd(was)} to {usd(to)}, enough for its next run",
            "it keeps going; if it reaches this limit too, I will ask you",
        )
