"""bring_base: the base's newest commits come into a pull request's branch before a review reads it.

A review used to read the branch as it stood, and only the merge after a clean verdict found that the base had
moved under it. A conflict then cost a fix run and a whole new review, which could ask for changes the
verdict before it had not: a pull request could pass review again and again and never merge. So the loop
brings the base in first. A clean review is then of a commit that merges at once, and a conflict is found,
and resolved, before any review is spent on a commit that could not merge.
"""

from temporalio import activity
from temporalio.exceptions import ApplicationError

from meta_harness.run_service import code_host, provenance
from meta_harness.run_service.shared import BaseRequest, MergeResult


@activity.defn
async def bring_base(request: BaseRequest) -> MergeResult:
    """Brings the base into the pull request's branch. state is current (the branch holds the base's newest
    commit), head_changed (the base came in, and head_sha is the new head) or conflict (it does not merge in
    cleanly, and a fix run must resolve it). A local repo has no pull request to bring a base into: current."""
    if code_host.is_local(request.repo):
        return MergeResult(state="current", head_sha=request.head_sha)
    brought_by = provenance.Provenance(provenance.COMMIT, task=request.task_id)
    same = False
    try:
        repo, number = code_host.pr_number(request.pr_url)
        state, _, head, detail = await code_host.bring_base_in(
            repo, number, request.base, request.head_sha, provenance=brought_by
        )
        if state == "head_changed" and head:
            same = await code_host.same_diff(repo, request.base, request.head_sha, head)
    except code_host.CodeHostError as err:
        raise ApplicationError(str(err), type="CodeHostError", non_retryable=not err.retryable) from err
    activity.logger.info("bring_base: %s at %s: %s %s", request.pr_url, request.head_sha[:12], state, detail)
    return MergeResult(state=state, head_sha=head, detail=detail[:500], same_diff=same)
