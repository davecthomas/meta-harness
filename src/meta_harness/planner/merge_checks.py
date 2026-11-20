"""A merged task's checks as the task judges them, for the console's Checks tab.

A merge to the default branch cancels the checks still running on the merge before it, and that merge's
deploy then fails for want of them. Neither is the earlier merge's failure: the newer commit's run covers its
code too. The task's merge wait already judges a check that did not pass by the same check on the branch's
newest commit (`run_service.activities._merge_runs`); this does the same for what the operator reads, so a
replaced run shows the run that replaced it, not a red "failed".
"""

from typing import Any

from meta_harness.run_service import code_host


async def judged(repo: str, merge_sha: str, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The merge commit's checks, with each one that did not pass swapped for the same check on the default
    branch's newest commit when that commit holds the merge. A swapped check carries `replaced`: the commit
    and the conclusion of the run it stands in for. A canceled one with no newer run yet shows as queued."""
    unpassed = [
        c for c in checks if c["status"] == "completed" and c.get("conclusion") not in code_host.PASSING_CONCLUSIONS
    ]
    if not unpassed:
        return checks
    newest = await code_host.newest_containing(repo, "HEAD", merge_sha)
    if not newest or newest == merge_sha:
        return checks
    later = {c["name"]: c for c in await code_host.check_details(repo, newest)}
    out = []
    for check in checks:
        if check in unpassed and check["name"] in later:
            check = {**later[check["name"]], "replaced": {"sha": merge_sha, "conclusion": check.get("conclusion")}}
        elif check in unpassed and check.get("conclusion") == "cancelled":
            check = {
                **check,
                "status": "queued",
                "conclusion": None,
                "replaced": {"sha": merge_sha, "conclusion": "cancelled"},
            }
        out.append(check)
    return out
