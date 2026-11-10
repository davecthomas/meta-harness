"""resume_state: what a task an operator sent back to the queue picks up from.

A task whose workflow ended review_unresolved, failed or canceled, or ended ready and was asked to merge, gets
a new RunTask workflow. Its pull request, when it is still open, already holds the earlier work, so the new
workflow goes back into the review loop on it (or straight to the merge) rather than starting over. This
activity reads what that takes: the pull request's state, branch and head from the code host, the pair that
wrote it, and the task's earlier runs, so the new workflow's runs are numbered after them.
"""

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal

from temporalio import activity
from temporalio.exceptions import ApplicationError

from meta_harness import db
from meta_harness.run_service import code_host, routing
from meta_harness.run_service.shared import Attempt, Candidate, PublishResult
from meta_harness.run_service.task_activities import _task_api_error, _tasks
from meta_harness.tasks.client import TaskApiError

LOOP_TIERS = ("review", "fix")


@dataclass
class ResumeState:
    # The pull request now: open, merged, closed, or "" when the task has none (or its repo is a local one).
    pr_state: str = ""
    published: PublishResult | None = None
    # The pair that wrote the pull request, which makes its fix runs. None when it can no longer be resolved.
    author: Candidate | None = None
    # The task's earlier runs, oldest first.
    attempts: list[Attempt] = field(default_factory=list)


def _earlier_runs(task_id: str) -> list[Attempt]:
    with db.connect(autocommit=True) as conn:
        rows = conn.execute(
            "SELECT attempt_no, route_tier, candidate, id, decision, detail, spent_usd FROM run_service.run "
            "WHERE task_id = %s ORDER BY attempt_no",
            (task_id,),
        ).fetchall()
    return [
        Attempt(
            attempt=row[0],
            tier=row[1],
            candidate=row[2],
            run_id=row[3],
            decision=row[4],
            detail=row[5] or "",
            spent_usd=str(row[6] if row[6] is not None else Decimal(0)),
        )
        for row in rows
    ]


def author_of(attempts: list[Attempt]) -> Candidate | None:
    """The pair of the last run that was not a review or a fix and finished: the one whose work was published."""
    for attempt in reversed(attempts):
        if attempt.tier not in LOOP_TIERS and attempt.decision == "finished":
            try:
                return routing.pinned(routing.replace_engine(attempt.candidate))
            except routing.CandidateError:
                return None
    return None


@activity.defn
async def resume_state(task_id: str) -> ResumeState:
    try:
        task = await asyncio.to_thread(lambda: _tasks().get(task_id))
    except TaskApiError as err:
        raise _task_api_error(err) from err
    attempts = await asyncio.to_thread(_earlier_runs, task_id)
    pr_url = task.get("pr_url") or ""
    if not pr_url or code_host.is_local(task["repo"]):
        return ResumeState(attempts=attempts)
    try:
        repo, number = code_host.pr_number(pr_url)
        pull = await code_host.pull_request(repo, number)
    except code_host.CodeHostError as err:
        raise ApplicationError(str(err), type="CodeHostError", non_retryable=not err.retryable) from err
    state = "merged" if pull.get("merged") else str(pull.get("state") or "")
    published = PublishResult(
        branch=str(pull.get("head", {}).get("ref", "")),
        base=str(pull.get("base", {}).get("ref", "")),
        commit=str(pull.get("head", {}).get("sha", "")),
        files=[],
        pr_url=pr_url,
        created=False,
    )
    return ResumeState(pr_state=state, published=published, author=author_of(attempts), attempts=attempts)
