"""A task whose pull request merged or closed, which no goal's sweep covers, is closed or canceled to match."""

from meta_harness.planner import sweep
from meta_harness.supervisor.remedies import Context, Outcome, Remedy


class EndAtPullRequest(Remedy):
    id = "end_at_pull_request"

    async def apply(self, ctx: Context) -> Outcome:
        sup, task = ctx.supervisor, ctx.task
        await sweep._record_end(ctx.goal, task, ctx.pull, sup.tasks, sup.events, sweep.SweepResult(""))
        sup.forget_reads(task["id"])
        return Outcome("done", "ended it to match its pull request", "nothing: it is over")
