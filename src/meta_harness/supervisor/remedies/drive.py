"""Sending a parked or failed task back to the queue for one more go: its merge, its review loop, or a whole
new run after a failure on the system's side. Each send is one of the task's drives, which the Task API
counts and caps (tasks.store.MAX_DRIVES); past the cap rules.decide asks an operator instead."""

import asyncio

from meta_harness.supervisor import failures
from meta_harness.supervisor.remedies import Context, Outcome, Remedy
from meta_harness.tasks.store import MAX_DRIVES


class Drive(Remedy):
    did = ""
    next = ""
    # The Task API's drive action, when it is not the remedy's own id, and the complexity the task's next runs
    # route at (None: as it was).
    action = ""
    complexity: str | None = None

    def of(self, task: dict) -> int | None:
        return MAX_DRIVES

    def tries(self, task: dict) -> int:
        return int(task.get("drives") or 0)

    def counted(self, task: dict) -> bool:
        return True

    async def apply(self, ctx: Context) -> Outcome:
        from meta_harness.supervisor.loop import spent_so_far

        sup, task = ctx.supervisor, ctx.task
        spent = await asyncio.to_thread(spent_so_far, sup.events, task["id"])
        await asyncio.to_thread(
            sup.tasks.drive,
            task["id"],
            self.action or self.id,
            ctx.decision.reason,
            str(spent),
            self.counted(task),
            self.complexity,
        )
        sup.forget_reads(task["id"])
        return Outcome("started", self.did, self.next)


class Merge(Drive):
    id = "merge"
    did = "sent it back to merge its reviewed pull request"
    next = "it merges once its checks pass; a conflict with main goes to a fix run first"


class Review(Drive):
    id = "review"
    did = "sent it back through its review loop"
    next = "its checks and review run again, and a fix run settles what they find"


class Retry(Drive):
    id = "retry"
    did = "ran it again, after a wait"
    next = "it starts from the queue; if it fails on the system's side again, it waits longer next time"

    def counted(self, task: dict) -> bool:
        # A retry after the worker's own restart is not one of the task's tries.
        return not failures.restart(task.get("status_reason"))
