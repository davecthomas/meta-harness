"""Waiting: the task is moving on its own (its checks have not reported yet), so this pass does nothing and
records nothing. The next pass looks again."""

from meta_harness.supervisor.remedies import Context, Outcome, Remedy


class Wait(Remedy):
    id = "wait"

    async def apply(self, ctx: Context) -> Outcome | None:
        return None
