"""How an open pull request stands for a merge, from what the code host says of it and its head's checks."""

from typing import Any

# How a parked task's open pull request stands. unknown: the code host has not worked it out, or was not asked.
MERGEABLE = ("clean", "conflict", "checks_failed", "checks_pending", "blocked", "unknown")
# GitHub's mergeable_state values under which a merge is worth asking for. behind is one: the merge step
# brings the base in first.
MERGEABLE_STATES = ("clean", "unstable", "has_hooks", "behind")


def mergeable(pull: dict[str, Any], checks: str) -> str:
    """One of MERGEABLE for an open pull request. checks is its head commit's checks: passed, failed, pending
    or none (code_host.checks_state).

    A conflict comes first, since nothing else matters until it is resolved, then the checks. A pull request
    GitHub blocks while its required checks have not reported is waiting for them, and one it blocks with
    its checks passed needs something a person gives: an approval, or a rule lifted.
    """
    state = pull.get("mergeable_state")
    if pull.get("mergeable") is False or state == "dirty":
        return "conflict"
    if checks == "failed":
        return "checks_failed"
    if checks == "pending":
        return "checks_pending"
    if pull.get("draft"):
        return "blocked"
    if state in MERGEABLE_STATES:
        return "clean"
    if state == "blocked":
        return "checks_pending" if checks == "none" else "blocked"
    return "unknown"
