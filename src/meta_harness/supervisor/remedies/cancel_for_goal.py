"""A task that has not started, of a goal that ended, is canceled: nothing will ever start it."""

import asyncio

from meta_harness.supervisor.remedies import Context, Outcome, Remedy


class CancelForGoal(Remedy):
    id = "cancel_for_goal"

    async def apply(self, ctx: Context) -> Outcome:
        await asyncio.to_thread(ctx.supervisor.tasks.cancel, ctx.task["id"], ctx.decision.reason, "supervisor")
        return Outcome("done", "canceled it", "nothing: its goal is over")
