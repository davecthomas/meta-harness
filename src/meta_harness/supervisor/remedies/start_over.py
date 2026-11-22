"""The remedy that starts a task over on the current base, once, before an operator is asked.

A task parked at a pull request the base has left behind (it conflicts, and the fix runs did not resolve it),
or one whose review never came clean after its fix rounds, has one step left that needs no person: close the
pull request, delete its branch, and run the task fresh from the base as it is now. An operator's Start over
does the same. The fresh run meets the task's spend limit anew, as an operator's does, which is why this is
tried once per task and no more: the task policy's `remedies.start_over` (0 turns it off), counted in the
ledger.
"""

from meta_harness.config.kinds import remedy_tries
from meta_harness.run_service import routing
from meta_harness.supervisor import ledger
from meta_harness.supervisor.remedies import Context, Outcome
from meta_harness.supervisor.remedies.drive import Drive


class StartOver(Drive):
    id = "start_over"
    action = "restart"
    did = "closed its pull request and started it over on the current base"
    next = "it runs fresh, with a new pull request; if that does not come clean either, I will ask you"

    def of(self, task: dict) -> int | None:
        return remedy_tries(routing.task_policy(), self.id)

    def tries(self, task: dict) -> int:
        return ledger.tries(task["id"], self.id)

    async def apply(self, ctx: Context) -> Outcome:
        import asyncio

        sup, task = ctx.supervisor, ctx.task
        # Nothing carried: the fresh run has the task's whole spend limit, once.
        await asyncio.to_thread(sup.tasks.drive, task["id"], self.action, ctx.decision.reason, "0", False)
        sup.forget_reads(task["id"])
        return Outcome("started", self.did, self.next)
