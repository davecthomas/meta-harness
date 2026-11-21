"""The moves an operator is offered, best first, from where the pull request really stands."""

import dataclasses

from meta_harness.supervisor import moves
from meta_harness.supervisor.rules import Facts, decide

from .test_rules import _every

# The task behind PR #437 as the supervisor found it: twelve reviews, four of them clean, the last with two
# findings, a pull request that conflicts with its base, a stronger model already tried, no tries left.
STUCK = Facts(
    "review_unresolved",
    pull="open",
    goal="active",
    mergeable="conflict",
    drives_left=False,
    reviews=12,
    clean_reviews=4,
    findings_open=2,
)


def test_a_conflicting_pull_request_is_offered_resolve_and_merge_first_and_never_a_merge_as_it_is():
    decision = decide(STUCK)

    assert (decision.owner, decision.situation) == ("operator", "review_unresolved_conflict")
    assert decision.moves == ("resolve_and_merge", "cancel")
    assert "conflicts with its base" in decision.reason and decision.ask.startswith("Resolve the conflict and merge")
    assert decision.saw == "4 of its 12 reviews passed, and the last left 2 findings open"


def test_the_supervisor_resolves_a_conflict_itself_while_it_has_tries():
    decision = decide(dataclasses.replace(STUCK, drives_left=True))

    assert (decision.owner, decision.action) == ("supervisor", "review")
    assert decision.moves == (), "moves are an operator's"


def test_an_unsettled_review_with_no_conflict_is_offered_a_stronger_model_first_while_one_has_not_tried():
    open_findings = dataclasses.replace(STUCK, mergeable="clean")

    assert decide(open_findings).moves == ("retry", "merge_as_is", "cancel")
    untried = decide(dataclasses.replace(open_findings, fix_again_left=True))
    assert untried.moves == ("retry_stronger", "retry", "merge_as_is", "cancel")


def test_a_review_that_passed_the_commit_the_pull_request_is_at_is_offered_its_merge():
    passed = dataclasses.replace(STUCK, clean_review=True, mergeable="clean")

    decision = decide(passed)

    assert decision.moves == ("merge", "cancel") and decision.ask == "Merge it: its review is clean."
    conflicting = decide(dataclasses.replace(passed, mergeable="conflict"))
    assert conflicting.moves == ("resolve_and_merge", "cancel")


def test_a_ready_task_is_offered_its_merge_and_one_the_code_host_refuses_its_pull_request_first():
    ready = Facts("ready", pull="open", goal="active")
    assert decide(ready).moves == ("merge",)
    refused = dataclasses.replace(ready, auto_merge=True, mergeable="blocked")
    assert decide(refused).moves == ("open_pull_request", "merge")


def test_every_move_offered_is_a_known_one_and_only_an_operator_is_offered_any():
    for facts in _every():
        decision = decide(facts)
        assert set(decision.moves) <= set(moves.MOVES), f"{facts}: {decision.moves}"
        if decision.owner != "operator":
            assert decision.moves == (), f"{facts}: moves offered to {decision.owner}"
        assert "merge_as_is" not in decision.moves or facts.mergeable != "conflict", (
            f"{facts}: a pull request that conflicts cannot merge as it is"
        )


def test_the_account_of_one_review_reads_plainly_and_a_task_with_none_has_no_account():
    one = Facts("ready", pull="open", goal="active", reviews=1, clean_reviews=1)
    assert moves.account(one) == "its review passed"
    assert moves.account(dataclasses.replace(one, clean_reviews=0, findings_open=1)) == "its review left 1 finding open"
    assert moves.account(Facts("ready", pull="open")) == "" and moves.account(Facts("failed", reviews=3)) == ""


def test_a_tasks_reviews_are_counted_from_its_events_and_an_unread_event_service_gives_none():
    from meta_harness.supervisor.loop import Reviews, reviews_seen

    class Events:
        def __init__(self, found):
            self.found = found

        def read(self, from_position, limit, **filters):
            assert filters == {"task_id": "t-1", "event_type": "pull_request_reviewed"}
            if isinstance(self.found, Exception):
                raise self.found
            return {"events": [{"payload": p} for p in self.found]}

    changes = {"verdict": "changes", "findings": 2, "head_sha": "e1e9"}
    clean = {"verdict": "clean", "findings": 0, "head_sha": "06da"}
    assert reviews_seen(Events([changes, clean, changes]), "t-1") == Reviews(rounds=3, clean=1, findings_open=2)
    assert reviews_seen(Events([changes, clean]), "t-1") == Reviews(rounds=2, clean=1, clean_head="06da")
    assert reviews_seen(Events([]), "t-1") == Reviews()
    assert reviews_seen(Events(RuntimeError("unreachable")), "t-1") == Reviews()


def test_a_task_out_of_turns_on_the_strongest_model_is_the_planners_to_split_while_it_may():
    too_large = Facts("failed", goal="active", failure="permanent", split_left=True)

    decision = decide(too_large)

    assert (decision.owner, decision.situation) == ("planner", "failed_too_large")
    assert "Goal Planner splits its issue" in decision.reason and decision.moves == ()
    spent = decide(dataclasses.replace(too_large, split_left=False))
    assert (spent.owner, spent.moves) == ("operator", ("retry", "cancel"))
    assert spent.ask == "Retry it, or cancel it if the work is no longer wanted."
    # A stronger model not yet tried comes first: the split is for when the strongest has run out.
    assert decide(dataclasses.replace(too_large, stronger_left=True)).action == "retry_stronger"


def test_a_run_out_of_turns_or_time_is_read_from_its_reason():
    from meta_harness.supervisor import failures

    assert failures.out_of_run("run 1 on harness:pi-careful/claude:opus ended for good: ended ERROR (max_turns): x")
    assert failures.out_of_run("ended ERROR (max_minutes)")
    assert not failures.out_of_run("ended ERROR (spend_limit)") and not failures.out_of_run(None)
