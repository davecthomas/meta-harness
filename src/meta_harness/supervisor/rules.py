"""Who has a task, and what the supervisor does about it: one pure decision from the task's facts.

Every other part of the control plane moves a task when something happens to it: a workflow step ends, a
sweep sees an issue close, an operator presses a button. A task whose event never comes sits where it is.
The supervisor works the other way. Each pass it looks at where every open task stands now, and `decide`
answers one question for each: who has it?

    workflow    its RunTask workflow is running and will move it
    scheduler   it is queued, and an admission pass starts it or records why it holds it
    planner     a goal's sweep moves it: releases it when its dependencies close, ends it at its pull request
    supervisor  no one else has it: the supervisor's own action moves it, or it waits on something only
                the supervisor is watching, and looks again each pass (Decision.action)
    operator    it waits on a person, and `ask` says exactly what for

There is no sixth answer. `decide` is total: every combination of a task's status, what it waits on, its
workflow's state, its pull request's state and its goal's status gets an owner, and tests/supervisor walks
all of them to prove it. A new status or sub-state with no rule fails that test, which is what keeps a task
from being left with no one again.
"""

from dataclasses import dataclass

OWNERS = ("workflow", "scheduler", "planner", "supervisor", "operator")
# The task statuses the supervisor looks at: every one but closed, which is the only end with nothing left.
OPEN_STATUSES = ("queued", "blocked", "running", "taken_over", "ready", "review_unresolved", "failed", "canceled")
# What a workflow is, as the supervisor reads it from Temporal. unknown: Temporal could not be asked.
WORKFLOWS = ("running", "ended", "none", "unknown")
# What a pull request is on the code host. none: the task has none. unknown: the code host could not be asked.
PULLS = ("none", "open", "merged", "closed", "unknown")
# A goal's status, or "none" for a task of no goal.
GOALS = ("none", "planning", "active", "paused", "stalled", "budget_exhausted", "met", "canceled")
# The goals whose sweep moves their tasks.
SWEPT = ("active", "budget_exhausted")
WAITING_ON = ("", "budget", "review", "operator_review")
# What the supervisor does itself. None: nothing, someone else has the task.
ACTIONS = ("recover", "end_at_pull_request", "cancel_for_goal", "merge", "review", "wait")
# How a parked task's open pull request stands for a merge (pulls.MERGEABLE).
MERGEABLE = ("clean", "conflict", "checks_failed", "checks_pending", "blocked", "unknown")


@dataclass(frozen=True)
class Facts:
    """Where a task stands, from the task service, Temporal, the code host and the planner."""

    status: str
    workflow: str = "none"
    pull: str = "none"
    goal: str = "none"
    waiting_on: str = ""
    cancel_requested: bool = False
    # Whether the scheduler has started a workflow for the queued task: it holds a place for it.
    admitted: bool = False
    # Whether the task's goal merges clean pull requests itself.
    auto_merge: bool = False
    # For a parked task with an open pull request: how the pull request stands for a merge.
    mergeable: str = "unknown"
    # Whether the supervisor may still send the task back by itself: it has not used up its tries.
    drives_left: bool = True
    # Whether the task has waited in its situation longer than a wait of the supervisor's may last.
    stale: bool = False


@dataclass(frozen=True)
class Decision:
    owner: str
    # The rule that decided: stable, for the record and for tests.
    situation: str
    reason: str
    # For an operator: what to do. Empty for every other owner.
    ask: str = ""
    # For the supervisor: what it does. Empty otherwise.
    action: str = ""


def decide(facts: Facts) -> Decision:
    """Who has the task, and what the supervisor does when it is the supervisor."""
    status = facts.status
    if status in ("running", "taken_over"):
        return _running(facts)
    if status == "queued":
        return _queued(facts)
    if status == "blocked":
        return _blocked(facts)
    if status in ("ready", "review_unresolved"):
        return _parked(facts)
    if status == "failed":
        return Decision(
            "operator",
            "failed",
            "the task failed, and nothing retries it",
            ask="Read why it failed on its page, then retry it, or close its issue if the work is no longer wanted.",
        )
    if status == "canceled":
        if facts.pull == "open":
            return Decision(
                "operator",
                "canceled_with_pull_request",
                "the task was canceled and its pull request is still open",
                ask="Merge or close the pull request on the code host: no task will.",
            )
        return Decision("planner", "canceled", "the task was canceled: nothing is left to do for it")
    raise ValueError(f"no rule for a {status} task")


def _running(facts: Facts) -> Decision:
    gone = facts.workflow in ("ended", "none")
    if gone:
        why = (
            "its RunTask workflow ended without ending the task"
            if facts.workflow == "ended"
            else "it has no RunTask workflow"
        )
        return Decision("supervisor", "no_workflow", why, action="recover")
    if facts.workflow == "unknown":
        return Decision("workflow", "workflow_unknown", "Temporal could not be asked whether its workflow is running")
    if facts.cancel_requested:
        return Decision("workflow", "stopping", "it was asked to cancel, and its workflow is stopping its run")
    if facts.waiting_on == "budget":
        return Decision(
            "operator",
            "budget_hold",
            "its workflow is held: the task reached its spend limit, or its goal's budget",
            ask="Raise its spend limit or its goal's budget to carry on, or end the task.",
        )
    if facts.waiting_on == "review":
        return Decision(
            "operator",
            "review_hold",
            "its review asks for changes, and the fix run waits for the review hold to pass",
            ask="Dismiss findings that are wrong, or let the fix run start now.",
        )
    if facts.waiting_on == "operator_review":
        return Decision(
            "operator",
            "operator_review",
            "an operator took over this round's review, and its workflow waits for their verdict",
            ask="Submit the review's verdict, or release the review back to the agent.",
        )
    return Decision("workflow", "working", "its RunTask workflow is running")


def _queued(facts: Facts) -> Decision:
    if facts.goal in ("canceled", "met"):
        ended = "was canceled" if facts.goal == "canceled" else "was completed"
        return Decision(
            "supervisor", "goal_ended", f"its goal {ended}, so it will never start", action="cancel_for_goal"
        )
    if facts.admitted and facts.workflow in ("ended", "none"):
        why = (
            "the scheduler started it, and its RunTask workflow ended before the task was marked running"
            if facts.workflow == "ended"
            else "the scheduler started it, and it has no RunTask workflow"
        )
        return Decision("supervisor", "no_workflow", why, action="recover")
    if facts.admitted:
        return Decision(
            "workflow", "starting", "the scheduler started it, and its workflow is about to mark it running"
        )
    if facts.goal in ("paused", "stalled", "planning"):
        return Decision(
            "operator",
            "goal_not_active",
            f"its goal is {facts.goal}, so the scheduler holds it",
            ask="Resume the goal to let it start, or cancel the goal to end its tasks."
            if facts.goal != "planning"
            else "Approve the goal's plan to let it start.",
        )
    return Decision("scheduler", "queued", "it waits for the scheduler, which starts it or records why it holds it")


def _blocked(facts: Facts) -> Decision:
    if facts.goal in ("canceled", "met"):
        ended = "was canceled" if facts.goal == "canceled" else "was completed"
        return Decision(
            "supervisor", "goal_ended", f"its goal {ended}, so it will never start", action="cancel_for_goal"
        )
    if facts.goal in SWEPT:
        return Decision(
            "planner", "blocked", "it waits on other issues, and its goal's sweep queues it once they close"
        )
    if facts.goal == "none":
        return Decision(
            "operator",
            "blocked_no_goal",
            "it waits on other issues and has no goal, so no sweep will queue it",
            ask="Cancel it, or make it again without the dependency once the other issues are done.",
        )
    return Decision(
        "operator",
        "goal_not_active",
        f"it waits on other issues, and its goal is {facts.goal}, so no sweep will queue it",
        ask="Resume the goal, or cancel it to end its tasks."
        if facts.goal != "planning"
        else "Approve the goal's plan.",
    )


def _parked(facts: Facts) -> Decision:
    """A task whose workflow ended with its pull request open: ready, or review_unresolved."""
    if facts.pull in ("merged", "closed"):
        # A swept goal's sweep records the end; for any other task nothing would, so the supervisor does.
        if facts.goal in SWEPT:
            return Decision(
                "planner", "pull_request_ended", f"its pull request was {facts.pull}, and its goal's sweep ends it"
            )
        return Decision(
            "supervisor",
            "pull_request_ended",
            f"its pull request was {facts.pull}, and no goal's sweep covers it",
            action="end_at_pull_request",
        )
    if facts.pull == "none":
        return Decision(
            "operator",
            "parked_no_pull_request",
            "its workflow ended with work to merge, and it has no pull request on a code host",
            ask="Mark it done or cancel it on its page.",
        )
    if facts.status == "review_unresolved":
        return Decision(
            "operator",
            "review_unresolved",
            "its review loop ended without a clean review, and its pull request is open",
            ask="Read the last review, then merge it as it is, retry the review loop, or fix it on the branch.",
        )
    if facts.auto_merge and facts.goal in SWEPT:
        return _to_merge(facts)
    return Decision(
        "operator",
        "ready",
        "its review is clean and its pull request is open, and its goal does not merge by itself"
        if facts.goal != "none"
        else "its review is clean and its pull request is open, and it has no goal to merge it",
        ask="Merge the pull request, on the code host or with Merge on the task's page.",
    )


def _to_merge(facts: Facts) -> Decision:
    """A ready task of a goal that merges by itself: its workflow ended without merging, so the supervisor
    sees the merge through. It asks for the merge when the code host would take it, sends a conflict or a
    failed check back for a fix, and waits on checks that have not reported. It hands the task to an
    operator when the code host wants something only a person gives, when its own tries are used up, or
    when a wait has gone on too long."""
    how = facts.mergeable
    if how == "blocked":
        return Decision(
            "operator",
            "merge_blocked",
            "its checks passed and the code host still refuses the merge: a rule on the branch wants something more",
            ask="Open the pull request: give the approval or lift the rule it names, then press Merge on the task's page.",
        )
    if how in ("checks_pending", "unknown"):
        waiting = (
            "its pull request's checks have not all reported"
            if how == "checks_pending"
            else "the code host has not said whether its pull request can merge"
        )
        if facts.stale:
            return Decision(
                "operator",
                "merge_wait_stale",
                f"{waiting}, and that has not changed in a day",
                ask="Open the pull request and see what holds its checks: rerun them, or merge it yourself.",
            )
        return Decision(
            "supervisor", "merge_waiting", f"{waiting}; the supervisor looks again each pass", action="wait"
        )
    what = {
        "clean": ("merge_due", "the code host would take its merge now", "merge"),
        "conflict": ("merge_conflict", "its pull request conflicts with its base", "merge"),
        "checks_failed": ("merge_checks_failed", "its pull request's checks failed after its review", "review"),
    }[how]
    if not facts.drives_left:
        return Decision(
            "operator",
            "merge_tries_used",
            f"{what[1]}, and the supervisor has already sent it back as often as it may",
            ask="Read the task's last runs for why it keeps coming back, then merge it, retry it or fix it yourself.",
        )
    return Decision("supervisor", what[0], what[1], action=what[2])
