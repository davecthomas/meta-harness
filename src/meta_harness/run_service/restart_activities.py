"""close_for_restart: a task started over leaves its old pull request closed and its branch gone.

An operator starts a task over when its pull request is not worth finishing: it conflicts beyond repair, or the
work took a wrong turn. The task then runs fresh from the current base. Its old pull request is closed with a
comment saying why, and its branch is deleted, so the new run's branch and pull request are the only ones.
Only the control plane writes to the code host, so the workflow does this, never the task service.
"""

from dataclasses import dataclass

from temporalio import activity
from temporalio.exceptions import ApplicationError

from meta_harness import outbound
from meta_harness.run_service import code_host, provenance

SAID = (
    "Closed: an operator started this task over on the current base. A new pull request replaces this one, and "
    "this branch is deleted."
)


@dataclass
class RestartRequest:
    task_id: str
    repo: str
    pr_url: str
    branch: str


@activity.defn
async def close_for_restart(request: RestartRequest) -> str:
    """Closes the task's open pull request with a comment, and deletes its branch. Returns what it did. A
    local repo has no pull request on a code host: nothing is done. A pull request already closed, or a branch
    already gone, is left as it is, so a retried activity repeats nothing."""
    if code_host.is_local(request.repo):
        return "a local repo has no pull request to close"
    signed = provenance.Provenance(provenance.COMMENT, task=request.task_id)
    try:
        repo, number = code_host.pr_number(request.pr_url)
        body = await outbound.prepare(SAID, outbound.Context(outbound.GITHUB, outbound.COMMENT, outbound.FIXED))
        closed = await code_host.close_pull_request(repo, number, body, request.branch, provenance=signed)
    except code_host.CodeHostError as err:
        raise ApplicationError(str(err), type="CodeHostError", non_retryable=not err.retryable) from err
    activity.logger.info("close_for_restart: %s: %s", request.pr_url, closed)
    return closed
