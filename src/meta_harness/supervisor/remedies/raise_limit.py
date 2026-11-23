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


def asked(task: dict) -> tuple[Decimal, Decimal] | None:
    """The held task's spend limit and the limit its next run needs, or None when the hold is not for the
    task's own limit. Both are the task's whole limit, as the Task API holds it: the hold's own sentence
    counts from what the task had left when its workflow started, which is less by what earlier runs spent."""
    request = budget_request.parse(task.get("status_reason"))
    if not request or request.get("for") != "task":
        return None
    limit = Decimal(str(task.get("max_usd") or request["limit_usd"]))
    return limit, limit + Decimal(request["add_usd"])


def over_ceiling(task: dict) -> bool:
    """Whether the held task's next run needs more than the task policy lets one task spend: no raise can
    lift the hold."""
    from meta_harness.config import store as config_store

    found = asked(task)
    return found is not None and found[1] > config_store.task_spend_ceiling()


def raise_to(task: dict, goal: dict | None) -> Decimal | None:
    """The limit the supervisor may raise the held task to, or None when it may not: the hold is not for the
    task's own limit, the run needs more than twice it or more than one task may spend, or the goal's budget
    has no room for the raise."""
    found = asked(task)
    if found is None:
        return None
    limit, to = found
    if limit <= 0 or to > limit * MOST or over_ceiling(task):
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
            raise ValueError("the raise this hold needs is more than the Task Manager may make")
        was = asked(ctx.task)[0]
        await asyncio.to_thread(ctx.supervisor.tasks.budget, ctx.task["id"], "raise", str(to), "supervisor")
        return Outcome(
            "started",
            f"raised its spend limit from {usd(was)} to {usd(to)}, enough for its next run",
            "it keeps going; if it reaches this limit too, I will ask you",
        )
