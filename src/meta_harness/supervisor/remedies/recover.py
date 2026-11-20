"""A task with no workflow behind it goes back to the queue, to pick up where its pull request stands, with
what it spent carried over. One asked to cancel is canceled instead. The Task API counts recoveries and
fails a task past its cap (tasks.store.MAX_RECOVERIES)."""

import asyncio

from meta_harness.supervisor.remedies import Context, Outcome, Remedy
from meta_harness.tasks.store import MAX_RECOVERIES


class Recover(Remedy):
    id = "recover"

    def of(self, task: dict) -> int | None:
        return MAX_RECOVERIES

    def tries(self, task: dict) -> int:
        return int(task.get("recoveries") or 0)

    async def apply(self, ctx: Context) -> Outcome:
        from meta_harness.supervisor.loop import spent_so_far

        sup, task = ctx.supervisor, ctx.task
        spent = await asyncio.to_thread(spent_so_far, sup.events, task["id"])
        found = await asyncio.to_thread(sup.tasks.recover, task["id"], ctx.decision.reason, str(spent))
        if found["status"] == "queued":
            return Outcome(
                "started",
                "put it back in the queue, with what it spent carried over",
                "it starts again where its pull request stands",
            )
        return Outcome("done", f"ended it {found['status']}", "nothing: it is over")
