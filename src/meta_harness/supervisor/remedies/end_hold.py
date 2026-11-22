"""The remedy that ends a budget hold no raise can lift.

A task held for its own spend limit, whose next run needs more than the task policy lets one task spend, has
no raise coming: the ceiling is a hard cap. Left alone it waits a day, holding a place among the running tasks
and every task that depends on it. So the supervisor answers the hold with "end", as an operator's End the
task does. The task ends where it stood, usually with its pull request open, and from there the rules for a
parked task apply: a stronger model's fix rounds, or a start-over on the current base.
"""

import asyncio

from meta_harness.supervisor.remedies import Context, Outcome, Remedy


class EndHold(Remedy):
    id = "end_hold"

    async def apply(self, ctx: Context) -> Outcome:
        await asyncio.to_thread(ctx.supervisor.tasks.budget, ctx.task["id"], "end", None, "supervisor")
        return Outcome(
            "started",
            "ended its wait for budget: its next run needs more than one task may spend",
            "it ends where it stands, and I take its next step from there",
        )
