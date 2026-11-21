"""The moves an operator is offered for a task, ordered by which is most likely to get it merged.

When the supervisor hands a task to a person, the ask used to be a sentence, and the console worked out its
own buttons from the task's failure message. Those two could disagree, and neither read how the pull
request stood: a pull request that conflicted with its base was offered "Read the last review". So the
supervisor now ranks the moves itself, from the same facts its rules decide on, and saves them with the
task's standing. The console shows them as buttons in this order, the first as the recommended one.

A move is something a button does in full. Reading is never a move. The order follows the design's order of
moves (docs/technical-design.md, Goal seeking): merge, clear what blocks the merge, fix, a stronger model,
then what only a person decides.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from meta_harness.supervisor.rules import Facts

# Every move, with what its button does. The console holds each one's label and confirmation.
MOVES = {
    "merge": "Merge the pull request: its review is clean.",
    "resolve_and_merge": (
        "Resolve the conflict with the base in a fix run, then run its checks and a review, and merge once the "
        "review is clean."
    ),
    "retry_stronger": "Run the review loop again with a stronger model writing the fixes.",
    "retry": "Send the task back through its review loop, or run it again when it has no pull request.",
    "merge_as_is": "Merge the pull request with the last review's findings unfixed.",
    "open_pull_request": "Open the pull request on the code host, for an approval or a rule only a person lifts.",
    "open_goal": "Open the task's goal, to resume it or approve its plan.",
    "start_over": (
        "Close the pull request, delete its branch, and run the task fresh from the current base, with a new "
        "pull request."
    ),
    "cancel": "Cancel the task. Its pull request stays open on the code host.",
}


def ranked(situation: str, facts: "Facts") -> tuple[str, ...]:
    """The moves for an operator's task in this situation, best first. Empty where the task's page holds the
    control already (a budget hold, a review hold) or the failure's own message offers the way out."""
    conflict = facts.mergeable == "conflict"
    if situation == "review_clean_conflict":
        return ("resolve_and_merge" if facts.mergeable in ("conflict", "unknown") else "merge", "cancel")
    if situation == "review_unresolved_conflict":
        stronger = ("retry_stronger",) if facts.fix_again_left else ()
        return ("resolve_and_merge", *stronger, "start_over", "cancel")
    if situation == "review_unresolved":
        if facts.fix_again_left:
            return ("retry_stronger", "retry", "merge_as_is", "start_over", "cancel")
        return ("retry", "merge_as_is", "start_over", "cancel")
    if situation == "ready":
        return ("merge",)
    if situation in ("merge_blocked", "merge_wait_stale"):
        return ("open_pull_request", "merge")
    if situation == "merge_tries_used":
        return ("resolve_and_merge", "start_over", "cancel") if conflict else ("merge", "retry", "cancel")
    if situation == "canceled_with_pull_request":
        return ("open_pull_request",)
    if situation == "goal_not_active" or (situation == "failed" and facts.failure == "transient"):
        return ("open_goal",)
    if situation in ("failed_long_ago", "failed_retries_used") or (
        situation == "failed" and facts.failure != "transient"
    ):
        return ("retry", "cancel")
    return ()


def account(facts: "Facts") -> str:
    """What the task's reviews came to, in a sentence, for a task parked at its pull request: how many
    passed and what the last left open. Empty when it has had no review."""
    if facts.status not in ("ready", "review_unresolved") or not facts.reviews:
        return ""
    passed = (
        f"{facts.clean_reviews} of its {facts.reviews} reviews passed"
        if facts.reviews > 1
        else ("its review passed" if facts.clean_reviews else "its review did not pass")
    )
    parts = [passed]
    if facts.findings_open:
        left = f"left {facts.findings_open} finding{'s' if facts.findings_open != 1 else ''} open"
        parts = [f"its review {left}"] if facts.reviews == 1 else [passed, f"the last {left}"]
    return ", and ".join(parts)
