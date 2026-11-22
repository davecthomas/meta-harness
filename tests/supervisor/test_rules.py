"""The supervisor's rules: every open task has someone who has it. These run without Postgres."""

import dataclasses
import itertools

from meta_harness.supervisor import failures, pulls
from meta_harness.supervisor.rules import (
    ACTIONS,
    DEPENDENCIES,
    FAILURES,
    GOALS,
    MERGEABLE,
    OPEN_STATUSES,
    OWNERS,
    PULLS,
    SWEPT,
    WAITING_ON,
    WORKFLOWS,
    Facts,
    decide,
)
from meta_harness.tasks import store as task_store


def _every() -> list[Facts]:
    """Every combination of facts. How a pull request stands for a merge, and the supervisor's tries and
    waits, are read only for a parked task with an open pull request, so they are varied only there."""
    flags = (False, True)
    found = []
    for status, workflow, pull, goal, waiting_on, cancel, admitted, auto_merge in itertools.product(
        OPEN_STATUSES, WORKFLOWS, PULLS, GOALS, WAITING_ON, flags, flags, flags
    ):
        base = Facts(status, workflow, pull, goal, waiting_on, cancel, admitted, auto_merge)
        if status in ("ready", "review_unresolved") and pull == "open":
            found += [
                dataclasses.replace(base, mergeable=how, drives_left=left, stale=stale)
                for how, left, stale in itertools.product(MERGEABLE, flags, flags)
            ]
        else:
            found.append(base)
    # Whether a failure was the system's, and when, is read only for a failed or unresolved task.
    failed = []
    for facts in found:
        if facts.status in ("failed", "review_unresolved"):
            failed += [
                dataclasses.replace(
                    facts,
                    failure=kind,
                    retry_due=due,
                    failed_long_ago=old,
                    stronger_left=strong,
                    fix_again_left=again,
                    clean_review=clean and facts.status == "review_unresolved",
                )
                for kind, due, old, strong, again, clean in itertools.product(
                    FAILURES, flags, flags, flags, flags, flags
                )
            ]
    # Where its dependencies stand is read only for a blocked task.
    blocked = [
        dataclasses.replace(facts, dependency=how, blocker="o/r#7")
        for facts in found
        if facts.status == "blocked"
        for how in DEPENDENCIES
    ]
    # Whether its spend limit may be raised is read only for a task held for budget.
    held = [dataclasses.replace(facts, raise_left=True) for facts in found if facts.waiting_on == "budget"]
    return found + failed + blocked + held


def test_the_rules_cover_every_status_a_task_can_be_left_in():
    """A status added to the task service with no rule here fails this test: no task is left with no one."""
    assert set(OPEN_STATUSES) == set(task_store.TRANSITIONS) - {"closed"}
    assert set(WAITING_ON) == {"", *task_store.WAITING_ON}


def test_every_combination_of_facts_has_an_owner_and_an_operator_is_always_told_what_to_do():
    for facts in _every():
        decision = decide(facts)
        assert decision.owner in OWNERS, facts
        assert decision.situation and decision.reason, facts
        if decision.owner == "operator":
            assert decision.ask, f"{facts}: an operator has it and is not told what to do"
            assert not decision.action
        elif decision.owner == "supervisor":
            assert decision.action in ACTIONS, f"{facts}: the supervisor has it and does nothing"
        else:
            assert not decision.ask and not decision.action, facts


def test_no_task_is_left_to_a_driver_that_is_not_there():
    for facts in _every():
        decision = decide(facts)
        if decision.owner == "workflow":
            assert facts.workflow in ("running", "unknown"), f"{facts}: left to a workflow that is gone"
        if decision.owner == "scheduler":
            assert facts.status == "queued" and facts.goal in ("none", *SWEPT), facts
        if decision.owner == "planner" and facts.status in ("blocked", "ready", "review_unresolved"):
            assert facts.goal in SWEPT, f"{facts}: left to a sweep that does not cover its goal"


def test_a_running_task_with_no_workflow_is_recovered_whatever_it_was_waiting_on():
    for workflow in ("ended", "none"):
        for waiting_on in WAITING_ON:
            for status in ("running", "taken_over"):
                decision = decide(Facts(status, workflow, waiting_on=waiting_on, cancel_requested=True))
                assert (decision.owner, decision.action) == ("supervisor", "recover")
    assert decide(Facts("running", "running")).owner == "workflow"
    assert decide(Facts("running", "unknown")).owner == "workflow", "an unreachable Temporal moves no one"
    assert decide(Facts("running", "running", waiting_on="budget")).situation == "budget_hold"


def test_a_queued_task_the_scheduler_started_and_lost_is_recovered_and_one_not_started_is_the_schedulers():
    assert decide(Facts("queued", "ended", goal="active", admitted=True)).action == "recover"
    assert decide(Facts("queued", "none", goal="active", admitted=True)).action == "recover"
    assert decide(Facts("queued", "running", goal="active", admitted=True)).owner == "workflow"
    assert decide(Facts("queued", goal="active")).owner == "scheduler"
    paused = decide(Facts("queued", goal="paused"))
    assert paused.owner == "operator" and "Resume the goal" in paused.ask


def test_a_task_of_an_ended_goal_that_has_not_started_is_canceled():
    for status in ("queued", "blocked"):
        for goal in ("canceled", "met"):
            decision = decide(Facts(status, goal=goal))
            assert (decision.owner, decision.action) == ("supervisor", "cancel_for_goal")
    assert decide(Facts("blocked", goal="active")).owner == "planner"
    assert decide(Facts("blocked")).owner == "operator", "a blocked task of no goal is released by no sweep"


def test_a_parked_task_whose_pull_request_ended_is_ended_by_the_sweep_or_by_the_supervisor():
    for status in ("ready", "review_unresolved"):
        for pull in ("merged", "closed"):
            assert decide(Facts(status, pull=pull, goal="active")).owner == "planner"
            for goal in ("none", "paused", "stalled", "met", "canceled", "planning"):
                decision = decide(Facts(status, pull=pull, goal=goal))
                assert (decision.owner, decision.action) == ("supervisor", "end_at_pull_request"), goal
    ready = decide(Facts("ready", pull="open", goal="active"))
    assert ready.owner == "operator" and "Merge the pull request" in ready.ask
    assert "does not merge by itself" in ready.reason
    assert decide(Facts("ready", pull="unknown", goal="none")).owner == "operator", "no one is moved on an unknown"


def _ready(how: str, **facts) -> Facts:
    return Facts("ready", pull="open", goal="active", auto_merge=True, mergeable=how, **facts)


def test_a_ready_task_of_a_goal_that_merges_by_itself_is_seen_through_its_merge():
    assert pulls.MERGEABLE == MERGEABLE
    assert (decide(_ready("clean")).owner, decide(_ready("clean")).action) == ("supervisor", "merge")
    conflict = decide(_ready("conflict"))
    assert (conflict.action, conflict.situation) == ("merge", "merge_conflict"), "the merge step fixes a conflict"
    assert decide(_ready("checks_failed")).action == "review", "failed checks go back through the review loop"
    for how in ("checks_pending", "unknown"):
        assert (decide(_ready(how)).owner, decide(_ready(how)).action) == ("supervisor", "wait")
        stale = decide(_ready(how, stale=True))
        assert stale.owner == "operator" and "has not changed in a day" in stale.reason
    blocked = decide(_ready("blocked"))
    assert blocked.owner == "operator" and "approval" in blocked.ask


def test_a_ready_task_held_for_an_operator_is_never_sent_to_merge_by_the_supervisor():
    """Its pull request changes files that run with the control plane's authority: only a person merges it."""
    for how in ("clean", "conflict", "checks_failed", "checks_pending"):
        held = decide(_ready(how, held_for_operator=True))
        assert (held.owner, held.situation, held.action) == ("operator", "merge_held", "")
        assert "Merge on the task's page" in held.ask
    from meta_harness.supervisor import moves

    assert moves.ranked("merge_held", _ready("clean", held_for_operator=True)) == ("open_pull_request", "merge")


def test_a_clean_pull_request_is_merged_whatever_the_tries_and_once_per_commit():
    merged = decide(_ready("clean", drives_left=False, head="95e21b3cb083aaaa"))
    assert (merged.owner, merged.action, merged.situation) == ("supervisor", "merge", "merge_due")
    assert merged.reason.endswith("at 95e21b3cb083"), "the commit is in the reason, so the ledger knows it was asked"
    refused = decide(_ready("clean", head="95e21b3cb083aaaa", merge_refused_at_head=True))
    assert (refused.owner, refused.situation) == ("operator", "merge_refused")
    from meta_harness.supervisor import failures, moves

    assert moves.ranked("merge_refused", _ready("clean")) == ("open_pull_request", "merge")
    assert failures.merge_waits_on_checks('405: {"message":"Required status check \\"zero-cost\\" is in progress."}')
    assert not failures.merge_waits_on_checks("405: At least 1 approving review is required")


def test_a_pull_request_going_nowhere_is_started_over_on_the_current_base_once_before_anyone_is_asked():
    """On 2026-11-22 three tasks sat unresolved behind conflicts their fix runs had not resolved, their tries
    used up, and six more waited on them: each needed a person to press Start over."""
    conflicted = Facts(
        "review_unresolved", pull="open", goal="active", mergeable="conflict", drives_left=False, start_over_left=True
    )
    over = decide(conflicted)
    assert (over.owner, over.action, over.situation) == ("supervisor", "start_over", "review_unresolved_conflict")
    assert "started over on the current base" in over.reason
    # A review that never came clean, with its fix rounds and its stronger model used: the same last step.
    spent = Facts("review_unresolved", pull="open", goal="active", mergeable="clean", start_over_left=True)
    assert decide(spent).action == "start_over"
    # A ready task whose conflict the fix runs did not resolve.
    assert decide(_ready("conflict", drives_left=False, start_over_left=True)).action == "start_over"

    # Once is all: after it, the task is an operator's, as before.
    assert decide(dataclasses.replace(conflicted, start_over_left=False)).owner == "operator"
    assert decide(_ready("conflict", drives_left=False)).situation == "merge_tries_used"
    # Tries it still has come first: a start-over is the last step, not the first.
    assert decide(dataclasses.replace(conflicted, drives_left=True)).action == "review"
    # A goal that asks first is offered it as the first button.
    asked = decide(dataclasses.replace(conflicted, autonomy="ask"))
    assert asked.owner == "operator" and asked.moves[0] == "start_over" and "start it over" in asked.ask
    # Not for a goal that is not being worked.
    assert decide(dataclasses.replace(conflicted, goal="paused")).action != "start_over"


def test_a_hold_no_raise_can_lift_is_ended_not_waited_out():
    held = Facts("running", workflow="running", goal="active", waiting_on="budget", over_ceiling=True)
    ended = decide(held)
    assert (ended.owner, ended.action, ended.situation) == ("supervisor", "end_hold", "budget_hold_ceiling")
    # A goal that asks first, or one not being worked, leaves it to the operator, who is told why and what to do.
    for other in (dataclasses.replace(held, autonomy="ask"), dataclasses.replace(held, goal="paused")):
        asked = decide(other)
        assert (asked.owner, asked.situation) == ("operator", "budget_hold_ceiling")
        assert "End the task" in asked.ask and "cannot be raised" in asked.reason
    # A raise that can be made still comes first.
    assert decide(dataclasses.replace(held, over_ceiling=False, raise_left=True)).action == "raise_limit"


def test_the_supervisors_own_tries_are_bounded_and_then_an_operator_is_asked():
    for how in ("conflict", "checks_failed"):
        spent = decide(_ready(how, drives_left=False))
        assert spent.owner == "operator" and spent.situation == "merge_tries_used"
        assert "as often as it may" in spent.reason


def test_a_goal_that_does_not_merge_by_itself_or_is_not_swept_leaves_its_ready_tasks_to_an_operator():
    assert decide(Facts("ready", pull="open", goal="active", mergeable="clean")).owner == "operator"
    for goal in ("paused", "stalled", "none"):
        assert decide(Facts("ready", pull="open", goal=goal, auto_merge=True, mergeable="clean")).owner == "operator"
    unresolved = decide(Facts("review_unresolved", pull="open", goal="active", auto_merge=True, mergeable="clean"))
    assert unresolved.owner == "operator", "a review that did not come clean is a person's call"


def test_how_a_pull_request_stands_for_a_merge_is_read_from_the_code_host():
    assert pulls.mergeable({"mergeable": False, "mergeable_state": "dirty"}, "passed") == "conflict"
    assert pulls.mergeable({"mergeable": True, "mergeable_state": "clean"}, "failed") == "checks_failed"
    assert pulls.mergeable({"mergeable": True, "mergeable_state": "blocked"}, "pending") == "checks_pending"
    assert pulls.mergeable({"mergeable": True, "mergeable_state": "blocked"}, "none") == "checks_pending", (
        "required checks that have not been created yet"
    )
    assert pulls.mergeable({"mergeable": True, "mergeable_state": "blocked"}, "passed") == "blocked"
    for state in ("clean", "unstable", "has_hooks", "behind"):
        assert pulls.mergeable({"mergeable": True, "mergeable_state": state}, "passed") == "clean"
    assert pulls.mergeable({"mergeable": None, "mergeable_state": "unknown"}, "passed") == "unknown"
    assert pulls.mergeable({"mergeable": True, "mergeable_state": "clean", "draft": True}, "passed") == "blocked"


def test_a_failure_on_the_systems_side_is_retried_after_a_wait_and_the_tasks_own_is_a_persons():
    for goal in ("none", "active", "budget_exhausted"):
        due = decide(Facts("failed", goal=goal, failure="transient", retry_due=True))
        assert (due.owner, due.action) == ("supervisor", "retry"), goal
        waiting = decide(Facts("failed", goal=goal, failure="transient"))
        assert (waiting.owner, waiting.action, waiting.situation) == ("supervisor", "wait", "retry_waiting")
    own = decide(Facts("failed", goal="active", failure="permanent", retry_due=True))
    assert own.owner == "operator" and "would fail the same way" in own.reason
    used = decide(Facts("failed", goal="active", failure="transient", retry_due=True, drives_left=False))
    assert used.owner == "operator" and used.situation == "failed_retries_used"
    old = decide(Facts("failed", goal="active", failure="transient", retry_due=True, failed_long_ago=True))
    assert old.owner == "operator" and old.situation == "failed_long_ago"
    paused = decide(Facts("failed", goal="paused", failure="transient", retry_due=True))
    assert paused.owner == "operator" and paused.ask == "Resume the goal, then retry the task."
    assert decide(Facts("failed", goal="canceled", failure="transient", retry_due=True)).owner == "planner"


def test_a_review_loop_the_system_stopped_goes_back_through_it():
    stopped = Facts("review_unresolved", pull="open", goal="active", failure="transient", retry_due=True)
    assert (decide(stopped).owner, decide(stopped).action) == ("supervisor", "review")
    assert decide(dataclasses.replace(stopped, retry_due=False)).action == "wait"
    assert decide(dataclasses.replace(stopped, drives_left=False)).owner == "operator"
    assert decide(dataclasses.replace(stopped, failure="permanent")).owner == "operator", "its findings stand"


def test_only_the_named_failures_of_the_system_are_transient():
    transient = [
        "TaskFailed: run 1 on harness:pi-careful/claude:claude-opus-5-5 ended for good: run_harness failed (WorkerShutdown): WorkerShutdown: Worker is shutting down",
        "TaskFailed: every candidate failed (3 runs)",
        "TaskFailed: run 2 on harness:a/b:c ended for good: ended ERROR (model_error): the provider answered 529",
        "TaskFailed: run 1 on harness:a/b:c ended for good: run_harness failed (CodeHostError): GitHub answered 502: Bad Gateway",
        "TaskFailed: run 1 on harness:a/b:c ended for good: run_harness failed (SandboxUnavailable): docker did not start",
        "the review loop stopped: CodeHostError: GitHub API rate limit exceeded",
        "the review loop stopped: activity task timed out",
        "TaskFailed: none of the 3 attempts finished",
    ]
    permanent = [
        "TaskFailed: run 1 on harness:a/b:c ended for good: run_harness failed (HarnessConfigError): HarnessConfigError: the HTTP Gateway refused the run grant (422)",
        "TaskFailed: the task spent its $10.00 spend limit after 4 runs",
        "TaskFailed: run 2 finished, but pi changed nothing in o/r",
        "TaskFailed: run 1 on harness:a/b:c ended for good: ended ERROR (max_turns): out of turns",
        "the review loop used its 3 fix rounds",
        "it has no RunTask workflow, for the 4th time: its workflows keep ending without finishing, so it was not started again",
        "deploy failed on the merge commit",
        "",
        None,
    ]
    for reason in transient:
        assert failures.kind(reason) == "transient", reason
    for reason in permanent:
        assert failures.kind(reason) == "permanent", reason
    # A reason that names the task's own limit is the task's, even with a system error beside it.
    assert failures.kind("the task spent its $10.00 spend limit after a GatewayUnavailable") == "permanent"


def test_a_blocked_task_behind_a_stuck_or_unheld_issue_is_an_operators_and_names_the_issue():
    stuck = decide(Facts("blocked", goal="active", dependency="stuck", blocker="o/r#7"))
    assert stuck.owner == "operator" and "it waits on o/r#7, whose task is not moving" in stuck.reason
    unheld = decide(Facts("blocked", goal="active", dependency="unheld", blocker="o/r#9"))
    assert unheld.owner == "operator" and "o/r#9, which no goal holds" in unheld.reason
    assert decide(Facts("blocked", goal="active")).owner == "planner"


def test_a_task_that_failed_on_its_own_tries_a_stronger_model_once_before_anyone_is_asked():
    own = Facts("failed", goal="active", failure="permanent")
    assert decide(dataclasses.replace(own, stronger_left=True)).action == "retry_stronger"
    assert decide(own).owner == "operator", "once its stronger try is used, an operator decides"
    assert decide(dataclasses.replace(own, stronger_left=True, failed_long_ago=True)).owner == "operator"
    assert decide(dataclasses.replace(own, stronger_left=True, goal="paused")).owner == "operator"


def test_a_review_its_fixes_did_not_settle_gets_one_more_set_of_fix_rounds_before_anyone_is_asked():
    stuck = Facts("review_unresolved", pull="open", goal="active", failure="permanent")
    assert decide(dataclasses.replace(stuck, fix_again_left=True)).action == "fix_again"
    assert decide(stuck).situation == "review_unresolved"
    assert decide(dataclasses.replace(stuck, fix_again_left=True, drives_left=False)).owner == "operator"


def test_a_clean_review_a_conflict_kept_from_merging_goes_back_for_the_conflict_not_to_a_stronger_model():
    clean = Facts(
        "review_unresolved", pull="open", goal="active", failure="permanent", fix_again_left=True, clean_review=True
    )
    assert decide(clean).situation == "review_clean_conflict"
    assert decide(clean).action == "review", "a goal that does not merge by itself gets it back ready"
    assert decide(dataclasses.replace(clean, auto_merge=True)).action == "merge"
    spent = decide(dataclasses.replace(clean, drives_left=False))
    assert spent.owner == "operator" and spent.ask.startswith("Resolve the conflict and merge")


def test_the_reason_a_clean_review_blocked_by_a_conflict_leaves_is_read_as_one():
    from meta_harness.supervisor import failures

    reason = (
        "review round 11 by r came back clean at 06daae279d3d, but the pull request conflicts with its base. "
        "No fix round was left to resolve it: the loop used its 3 fix rounds"
    )
    assert failures.clean_blocked(reason) and failures.kind(reason) == "permanent"
    assert not failures.clean_blocked("the review loop used its 3 fix rounds")


def test_a_task_held_at_its_own_spend_limit_is_raised_once_within_bounds_then_its_operators():
    held = Facts("running", workflow="running", goal="active", waiting_on="budget")
    assert decide(dataclasses.replace(held, raise_left=True)).action == "raise_limit"
    assert decide(held).owner == "operator", "past the bounds, or once raised, an operator decides"
