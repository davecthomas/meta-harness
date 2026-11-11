"""The supervisor's rules: every open task has someone who has it. These run without Postgres."""

import itertools

from meta_harness.supervisor.rules import (
    ACTIONS,
    GOALS,
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
    flags = (False, True)
    return [
        Facts(status, workflow, pull, goal, waiting_on, cancel, admitted, auto_merge)
        for status, workflow, pull, goal, waiting_on, cancel, admitted, auto_merge in itertools.product(
            OPEN_STATUSES, WORKFLOWS, PULLS, GOALS, WAITING_ON, flags, flags, flags
        )
    ]


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
    assert decide(Facts("ready", pull="unknown", goal="none")).owner == "operator", "no one is moved on an unknown"
