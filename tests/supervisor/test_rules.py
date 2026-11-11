"""The supervisor's rules: every open task has someone who has it. These run without Postgres."""

import dataclasses
import itertools

from meta_harness.supervisor import failures, pulls
from meta_harness.supervisor.rules import (
    ACTIONS,
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
                dataclasses.replace(facts, failure=kind, retry_due=due, failed_long_ago=old)
                for kind, due, old in itertools.product(FAILURES, flags, flags)
            ]
    return found + failed


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


def test_the_supervisors_own_tries_are_bounded_and_then_an_operator_is_asked():
    for how in ("clean", "conflict", "checks_failed"):
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
