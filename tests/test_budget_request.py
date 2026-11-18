"""A budget hold's reason is written and read back by one module, so the amounts a page offers are the hold's."""

from decimal import Decimal

from meta_harness import budget_request


def test_a_hold_for_the_tasks_limit_reads_back_as_a_request_for_enough_for_the_run():
    why = budget_request.task_why(Decimal("0.40"), Decimal("3"))
    reason = budget_request.hold_reason("fix run 2", Decimal("1.6512"), why)
    assert reason == (
        "fix run 2 needs about $1.65, and the task has $0.40 left of its $3.00 spend limit. "
        "Raise the task's spend limit, or end the task"
    )

    assert budget_request.parse(reason) == {
        "for": "task",
        "what": "fix run 2",
        "needs_usd": "1.65",
        "left_usd": "0.40",
        "limit_usd": "3.00",
        # $1.25 short, asked for in whole dollars.
        "add_usd": "2",
        "to_usd": "5.00",
    }


def test_a_task_past_its_limit_asks_for_what_it_is_over_too_and_never_for_cents():
    over = budget_request.hold_reason("the review", Decimal("0.5"), budget_request.task_why(Decimal("-1.2"), 5))
    assert budget_request.parse(over)["add_usd"] == "2"
    close = budget_request.hold_reason("the review", Decimal("0.5"), budget_request.task_why(Decimal("0.45"), 5))
    assert budget_request.parse(close)["add_usd"] == "1"


def test_a_hold_for_the_goals_budget_names_the_goal_and_leaves_the_amount_to_its_resume():
    why = budget_request.goal_why("g-1", "9.5", "10", Decimal("0.3667"), 3)
    reason = budget_request.hold_reason("the first run", Decimal("0.1"), why)
    assert budget_request.for_goal(why) and "Raise the goal's budget, or end the task" in reason

    assert budget_request.parse(reason) == {
        "for": "goal",
        "what": "the first run",
        "needs_usd": "0.10",
        "goal_id": "g-1",
        "share_usd": "0.37",
        "running": 3,
    }


def test_any_other_reason_is_no_request():
    assert budget_request.parse(None) is None
    assert budget_request.parse("the budget was raised; carrying on with fix run 2") is None
