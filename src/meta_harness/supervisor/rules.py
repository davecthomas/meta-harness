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

from dataclasses import dataclass, replace

from meta_harness.supervisor import moves

OWNERS = ("workflow", "scheduler", "planner", "supervisor", "operator")
# Situations in which nothing is left to do for a task, and nobody has it: it was canceled, or its goal was and
# the task ended or is parked. Their decisions are recorded under the planner, whose sweep closes what is left
# of a goal, and a reader is told the task is over, not that the planner has work on it.
OVER = ("canceled", "failed_goal_ended", "goal_ended")


def over(owner: str, situation: str) -> bool:
    """Whether a task's standing says it is over: nobody has it, and nothing more is done for it."""
    return owner == "planner" and situation in OVER


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
ACTIONS = (
    "recover",
    "end_at_pull_request",
    "cancel_for_goal",
    "merge",
    "review",
    "retry",
    "wait",
    "retry_stronger",
    "fix_again",
    "raise_limit",
    "start_over",
    "end_hold",
)
# Where the issues a blocked task waits on stand. open: each is being worked, or will be. stuck: one's task
# failed, was canceled or ended unresolved, and waits on an operator. unheld: one is in no goal, so nothing
# is working on it.
DEPENDENCIES = ("open", "stuck", "unheld")
# Whether a failure was the system's or the task's (failures.kind).
FAILURES = ("transient", "permanent")
# The goals under which a task the supervisor retries would run: a task of any other goal would only be held.
RUNNABLE = ("none", "active", "budget_exhausted")
# How a parked task's open pull request stands for a merge (pulls.MERGEABLE).
MERGEABLE = ("clean", "conflict", "checks_failed", "checks_pending", "blocked", "unknown")


# The supervisor's actions a goal on `ask` is asked about first: what the action does, in words, and the
# offered action that does it. Housekeeping (recover, a wait, ending a task whose pull request ended) is done
# either way: nothing in it is a decision.
ASKED_FIRST = {
    "merge": ("merge its pull request", "merge"),
    "review": ("send it back through its review", "retry"),
    "retry": ("retry it", "retry"),
    "retry_stronger": ("retry it with a stronger model", "retry_stronger"),
    "fix_again": ("give its review one more set of fix rounds on a stronger model", "retry_stronger"),
    "start_over": ("close its pull request and start it over on the current base", "start_over"),
}


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
    # Whether the task's merge is held for an operator: its pull request changes a path that runs with the
    # control plane's own authority once merged, which no review merges alone.
    held_for_operator: bool = False
    # For a parked task with an open pull request: how the pull request stands for a merge.
    mergeable: str = "unknown"
    # Whether the supervisor may still send the task back by itself: it has not used up its tries.
    drives_left: bool = True
    # For a parked task with an open pull request: the commit it is at, and whether the supervisor has already
    # asked for the merge at that commit and been refused for something other than a check still running.
    head: str = ""
    merge_refused_at_head: bool = False
    # Whether the task has waited in its situation longer than a wait of the supervisor's may last.
    stale: bool = False
    # For a failed or unresolved task: whether what it failed on was the system's or its own.
    failure: str = "permanent"
    # Whether the wait before the supervisor's next try of a failed task has passed.
    retry_due: bool = False
    # Whether the task failed longer ago than the supervisor retries by itself: an old failure is a person's.
    failed_long_ago: bool = False
    # For a blocked task: where the issues it waits on stand, and which of them is in the way.
    dependency: str = "open"
    blocker: str = ""
    # Whether a remedy still has tries left for this task (the task policy's remedies, counted in the ledger):
    # a run on the complex tier for a task that failed on its own, and one more set of fix rounds on it for a
    # review the fixes did not settle.
    stronger_left: bool = False
    fix_again_left: bool = False
    # Whether the task may still be started over on the current base by the supervisor: once per task.
    start_over_left: bool = False
    # For an unresolved task: its last review came back clean, and only a conflict with its base kept it from
    # merging (failures.clean_blocked).
    clean_review: bool = False
    # For a task held at its own spend limit: whether the supervisor may raise it once, within twice the limit
    # and what its goal's budget has left.
    raise_left: bool = False
    # For a task held at its own spend limit: its next run needs more than one task may spend, so no raise
    # can lift the hold.
    over_ceiling: bool = False
    # For a task parked at its pull request: how many reviews it has had, how many of them passed, and how
    # many findings the last one left open (its pull_request_reviewed events).
    reviews: int = 0
    clean_reviews: int = 0
    findings_open: int = 0
    # How much the task's goal lets the supervisor do without asking: ask, standard or full (the planner's
    # store.AUTONOMY). A task of no goal is standard.
    autonomy: str = "standard"
    # For a failed task of a goal: its run stopped at its turn or time limit on the strongest tier, and the
    # planner may still split its issue (the task policy's `remedies.auto_split` tries).
    split_left: bool = False


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
    # For an operator: the moves offered, best first (moves.MOVES). Empty where the page holds the control.
    moves: tuple[str, ...] = ()
    # For a task parked at its pull request: what its reviews came to (moves.account).
    saw: str = ""


def decide(facts: Facts) -> Decision:
    """Who has the task, what the supervisor does when it is the supervisor, and what an operator is offered
    when it is an operator's."""
    decision = _decide(facts)
    if facts.autonomy == "ask" and decision.owner == "supervisor" and decision.action in ASKED_FIRST:
        # The goal asks before each of the supervisor's own actions: the action it would take is offered to
        # the operator as the first button, with the others that apply.
        does, first = ASKED_FIRST[decision.action]
        if first == "merge" and facts.mergeable == "conflict":
            first = "resolve_and_merge"
        offered = (first, *(m for m in moves.ranked(decision.situation, facts) if m != first), "cancel")
        ask = f"I would {does}. Its goal asks before I act, so the first button does it."
        return replace(
            decision,
            owner="operator",
            action="",
            ask=ask,
            moves=tuple(dict.fromkeys(offered)),
            saw=moves.account(facts),
        )
    offered = moves.ranked(decision.situation, facts) if decision.owner == "operator" else ()
    return replace(decision, moves=offered, saw=moves.account(facts))


def _decide(facts: Facts) -> Decision:
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
        return _failed(facts)
    if status == "canceled":
        if facts.pull == "open" and facts.goal == "canceled":
            # Whoever canceled the goal chose to stop its work. Its pull requests stay open on the code host,
            # and none of them is something the operator is asked to act on.
            return Decision(
                "planner",
                "canceled",
                "the task and its goal were canceled, and its pull request stays open on the code host",
            )
        if facts.pull == "open":
            return Decision(
                "operator",
                "canceled_with_pull_request",
                "the task was canceled and its pull request is still open",
                ask="Merge or close the pull request on the code host: no task will.",
            )
        return Decision("planner", "canceled", "the task was canceled: nothing is left to do for it")
    raise ValueError(f"no rule for a {status} task")


def _failed(facts: Facts) -> Decision:
    if facts.goal in ("canceled", "met"):
        ended = "was canceled" if facts.goal == "canceled" else "was completed"
        return Decision("planner", "failed_goal_ended", f"the task failed, and its goal {ended}: nothing is left to do")
    if facts.failure == "transient" and facts.goal in RUNNABLE:
        if facts.failed_long_ago:
            return Decision(
                "operator",
                "failed_long_ago",
                "the task failed on the system's side, not its own, too long ago for the Task Manager to retry it by itself",
                ask="Retry it if the work is still wanted: it should run now. Otherwise close its issue.",
            )
        if not facts.drives_left:
            return Decision(
                "operator",
                "failed_retries_used",
                "the task failed on the system's side again after the Task Manager retried it as often as it may",
                ask="Something keeps failing under it: read its last runs and the logs it links, then retry it.",
            )
        if not facts.retry_due:
            return Decision(
                "supervisor",
                "retry_waiting",
                "the task failed on the system's side, not its own; the Task Manager retries it after a wait",
                action="wait",
            )
        return Decision("supervisor", "retry_due", "the task failed on the system's side, not its own", action="retry")
    if facts.failure == "permanent" and facts.goal in SWEPT and facts.split_left and not facts.stronger_left:
        return Decision(
            "planner",
            "failed_too_large",
            "its run ran out of turns or time on the strongest model. The Goal Planner splits its issue into "
            "smaller ones, which run in its place",
        )
    if facts.failure == "permanent" and facts.goal in RUNNABLE and facts.stronger_left and not facts.failed_long_ago:
        return Decision(
            "supervisor",
            "failed_try_stronger",
            "the task failed on its own account, and a stronger model has not tried it yet",
            action="retry_stronger",
        )
    return Decision(
        "operator",
        "failed",
        "the task failed, and another try would fail the same way"
        if facts.failure == "permanent"
        else f"the task failed on the system's side, and its goal is {facts.goal}, so a retry would not start",
        ask="Retry it, or cancel it if the work is no longer wanted."
        if facts.failure == "permanent"
        else "Resume the goal, then retry the task.",
    )


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
    if facts.goal == "canceled":
        # A canceled goal's work stops, whatever its task was doing: mid-run, or waiting on a budget nobody
        # will raise now. Its pull request, if it has one, stays open on the code host.
        return Decision(
            "supervisor", "goal_ended", "its goal was canceled, so its run is stopped", action="cancel_for_goal"
        )
    if facts.waiting_on == "budget" and facts.raise_left:
        return Decision(
            "supervisor",
            "budget_hold_raise",
            "the task reached its own spend limit, and a raise within twice that limit and its goal's budget covers its next run",
            action="raise_limit",
        )
    if facts.waiting_on == "budget" and facts.over_ceiling and facts.goal in SWEPT and facts.autonomy != "ask":
        # The ceiling is a hard cap, so nothing lifts this hold. Waiting out its day only holds a running
        # place and every task behind this one.
        return Decision(
            "supervisor",
            "budget_hold_ceiling",
            "its next run needs more than one task may spend, so no raise can lift its hold",
            action="end_hold",
        )
    if facts.waiting_on == "budget" and facts.over_ceiling:
        return Decision(
            "operator",
            "budget_hold_ceiling",
            "its workflow is held: its next run needs more than one task may spend, so its limit cannot be raised",
            ask="End the task: it ends with its pull request open, to retry or start over. Or raise the task "
            "policy's most for one task first.",
        )
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
    if facts.goal in SWEPT and facts.dependency == "stuck":
        return Decision(
            "operator",
            "blocked_on_stuck",
            f"it waits on {facts.blocker or 'an issue'}, whose task is not moving and waits on an operator",
            ask="Open that issue's task and retry or fix it: this one is queued once that issue closes.",
        )
    if facts.goal in SWEPT and facts.dependency == "unheld":
        return Decision(
            "operator",
            "blocked_on_unheld",
            f"it waits on {facts.blocker or 'an issue'}, which no goal holds, so nothing is working on it",
            ask="Add that issue to a goal, or close it if it is done: this one is queued once it closes.",
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
    if facts.goal == "canceled" and facts.pull not in ("merged", "closed"):
        return Decision(
            "planner",
            "goal_ended",
            "its goal was canceled, so nothing more is done for it: its pull request stays open on the code host",
        )
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
    stopped = facts.status == "review_unresolved" and facts.failure == "transient" and not facts.failed_long_ago
    if stopped and facts.goal in SWEPT:
        # Its review loop was stopped by the system, not by its findings: it goes back through the loop.
        if facts.drives_left and facts.retry_due:
            return Decision(
                "supervisor",
                "review_stopped",
                "its review loop was stopped by a failure on the system's side, not by its review",
                action="review",
            )
        if facts.drives_left:
            return Decision(
                "supervisor",
                "retry_waiting",
                "its review loop was stopped by a failure on the system's side; the Task Manager sends it back after a wait",
                action="wait",
            )
    if facts.status == "review_unresolved" and facts.clean_review:
        # The review passed: what is left is the conflict, which a fresh set of fix rounds resolves first. A
        # goal that merges by itself has it merged once a review is clean again; any other gets it back ready.
        passed = (
            "its review came back clean, and only a conflict with its base kept it from merging"
            if facts.mergeable in ("conflict", "unknown")
            else "its last review passed the commit its pull request is at, and it has not merged"
        )
        if facts.goal in SWEPT and facts.drives_left:
            return Decision(
                "supervisor",
                "review_clean_conflict",
                passed,
                action="merge" if facts.auto_merge else "review",
            )
        return Decision(
            "operator",
            "review_clean_conflict",
            passed,
            ask="Resolve the conflict and merge: a fix run resolves it, and it merges once a review is clean."
            if facts.mergeable in ("conflict", "unknown")
            else "Merge it: its review is clean.",
        )
    if facts.status == "review_unresolved" and facts.goal in SWEPT and facts.fix_again_left and facts.drives_left:
        return Decision(
            "supervisor",
            "review_unresolved_fix_again",
            "its review loop ended with findings its fixes did not settle, and a stronger model has not tried them",
            action="fix_again",
        )
    if facts.status == "review_unresolved" and facts.mergeable == "conflict":
        # Resolving a conflict needs no person: the review loop brings the base in and resolves it before it
        # reviews. Said apart for an operator, since nothing else on the task's page shows the conflict.
        if facts.goal in SWEPT and facts.drives_left:
            return Decision(
                "supervisor",
                "review_unresolved_conflict",
                "its review loop ended without a clean review, and its pull request conflicts with its base",
                action="review",
            )
        if facts.goal in SWEPT and facts.start_over_left:
            return _start_over(
                "review_unresolved_conflict",
                "its pull request conflicts with its base, and the fix runs sent to resolve that did not",
            )
        return Decision(
            "operator",
            "review_unresolved_conflict",
            "its review loop ended without a clean review, and its pull request conflicts with its base",
            ask="Resolve the conflict and merge: a fix run resolves it, and it merges once a review is clean.",
        )
    if facts.status == "review_unresolved" and facts.goal in SWEPT and facts.start_over_left:
        return _start_over(
            "review_unresolved", "its review loop ended without a clean review, and its fix rounds are used up"
        )
    if facts.status == "review_unresolved":
        return Decision(
            "operator",
            "review_unresolved",
            "its review loop ended without a clean review, and its pull request is open",
            ask="Retry it, with a stronger model when one has not tried, or merge it as it is.",
        )
    if facts.held_for_operator:
        return Decision(
            "operator",
            "merge_held",
            "its review is clean, and its pull request changes files that run with the control plane's own "
            "authority once merged, which only a person merges",
            ask="Read the change on the pull request, then press Merge on the task's page.",
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


def _start_over(situation: str, why: str) -> Decision:
    """The last step the supervisor takes by itself for a task parked at a pull request that is going nowhere:
    start it over on the base as it is now, once (remedies/start_over.py)."""
    return Decision("supervisor", situation, f"{why}, so it is started over on the current base", action="start_over")


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
            "its checks passed and the code host still refuses the merge: a rule on the branch is not met yet",
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
            "supervisor", "merge_waiting", f"{waiting}; the Task Manager looks again each pass", action="wait"
        )
    if how == "clean":
        # Asking for a merge the code host would take spends nothing and is none of the task's tries: its tries
        # are for work that failed, and on a busy base they are soon used up by the base moving under it. So it
        # is asked for whatever the count says, once per commit: refused at this commit already, it is a
        # person's.
        at = f" at {facts.head[:12]}" if facts.head else ""
        if facts.merge_refused_at_head:
            return Decision(
                "operator",
                "merge_refused",
                f"the code host says it would take its merge{at}, and refused it when the Task Manager asked",
                ask="Open the pull request to see what it still wants, then press Merge on the task's page.",
            )
        return Decision("supervisor", "merge_due", f"the code host would take its merge now{at}", action="merge")
    what = {
        "conflict": ("merge_conflict", "its pull request conflicts with its base", "merge"),
        "checks_failed": ("merge_checks_failed", "its pull request's checks failed after its review", "review"),
    }[how]
    if not facts.drives_left and how == "conflict" and facts.start_over_left:
        return _start_over("merge_conflict", f"{what[1]}, and the fix runs sent to resolve that did not")
    if not facts.drives_left:
        return Decision(
            "operator",
            "merge_tries_used",
            f"{what[1]}, and the Task Manager has already sent it back as often as it may",
            ask="Read the task's last runs for why it keeps coming back, then merge it, retry it or fix it yourself.",
        )
    return Decision("supervisor", what[0], what[1], action=what[2])
