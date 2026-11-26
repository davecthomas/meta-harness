"""Adversarial plan review: a second model tries to find what is wrong with a plan before it is approved."""

import asyncio

import pytest

from meta_harness.planner import actions, plan_review, planning_spend

from .test_planner import OPERATOR, migrated, registry  # noqa: F401
from .test_planning import _feature, _planned, draft
from .test_planning import world  # noqa: F401


async def _no_issues(goal: dict) -> list:
    return []


def _goal(client, goal: dict) -> dict:
    return client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()


def _plan(client, goal: dict) -> dict:
    return client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()


def _review(client, tasks, events, goal: dict) -> dict | None:
    return asyncio.run(plan_review.maybe_start(_goal(client, goal), _plan(client, goal)["plan"], tasks, events))


def _ends(client, tasks, events, goal: dict, task: dict, review: dict | None, status: str = "closed") -> dict | None:
    if review is not None:
        events.emit("review_submitted", review, task_id=task["id"])
    task["status"] = status
    return asyncio.run(plan_review.collect(_goal(client, goal), tasks, events, _no_issues))


ORDER = {
    "path": "api",
    "blocking": True,
    "body": "[order] api reads a table store creates.\nChange: Make api wait for store.",
}
ADVICE = {"path": None, "blocking": False, "body": "[check] No task tests an export end to end. Change: Add one."}


def test_a_goal_that_asks_before_each_step_has_its_plans_reviewed_unless_it_says_otherwise(world):  # noqa: F811
    client, *_ = world
    assert _feature(client, autonomy="ask")["adversarial_review"] is True
    assert _feature(client)["adversarial_review"] is False
    assert _feature(client, autonomy="ask", adversarial_review=False)["adversarial_review"] is False
    assert _feature(client, adversarial_review=True)["adversarial_review"] is True
    off = _feature(client)
    turned = client.put(f"/v1/goals/{off['id']}/plan/review-setting", json={"on": True}, headers=OPERATOR)
    assert turned.status_code == 200 and turned.json()["adversarial_review"] is True


def test_the_reviewer_is_of_another_model_family_than_the_planner():
    pairs = ["harness:pi-review/claude:claude-opus-5-5", "harness:pi-review/openai-responses:gpt-6-sol"]
    assert plan_review.engine_of(pairs[1]) == "openai-responses"
    assert plan_review.pick("harness:pi-plan/claude:claude-opus-5-5", pairs) == pairs[1]
    assert plan_review.pick("harness:pi-plan/openai-responses:gpt-6-sol", pairs) == pairs[0]
    assert plan_review.pick("harness:pi-plan/claude:claude-opus-5-5", pairs[:1]) is None


def test_a_reviews_findings_are_read_from_what_the_review_tool_records():
    found = plan_review.parse({"findings": [ORDER, ADVICE, {"body": "No kind given."}]}, 2)

    assert found[0] == {
        "id": "r2-1",
        "kind": "order",
        "severity": "blocking",
        "says": "api reads a table store creates.",
        "change": "Make api wait for store.",
        "tasks": ["api"],
        "status": "open",
    }
    assert (found[1]["kind"], found[1]["severity"], found[1]["tasks"]) == ("check", "advice", [])
    assert found[1]["change"] == "Add one."
    assert (found[2]["kind"], found[2]["says"], found[2]["change"]) == ("", "No kind given.", "")


def test_a_settled_plan_is_reviewed_revised_for_what_blocks_and_looked_at_again(world):  # noqa: F811
    client, events, _, tasks = world
    goal = _feature(client, autonomy="ask")
    _planned(client, events, tasks, goal, [draft("store"), draft("api")])

    task = _review(client, tasks, events, goal)

    assert task["task_type"] == "plan" and task["external_ref"] == f"plan-review:{goal['id']}:1"
    assert plan_review.engine_of(task["pin"]) != "claude", "never the planner's own model family"
    assert "Your job is to find what will make this plan fail" in task["description"], "the locked skill's text"
    assert "## api (" in task["description"] and "Build api." in task["description"]
    assert planning_spend.is_unasked(task), "its cost counts as planning done without asking"
    assert _review(client, tasks, events, goal) is None, "a version is reviewed once"
    waiting = _plan(client, goal)
    assert waiting["review"]["state"] == "running" and waiting["decision"]["state"] == "improving"
    assert waiting["decision"]["says"] == "gpt-6-sol is checking the plan for problems."
    assert waiting["decision"]["buttons"] == [], "nothing is asked while the reviewer reads"

    # It finds one problem that blocks and gives one piece of advice: the planner is sent back for them.
    collected = _ends(
        client,
        tasks,
        events,
        goal,
        task,
        {"verdict": "changes", "summary": "One order problem.", "findings": [ORDER, ADVICE]},
    )

    assert collected["state"] == "done" and [f["id"] for f in collected["findings"]] == ["r1-1", "r1-2"]
    revising = _plan(client, goal)
    revision = tasks.tasks[revising["planning_task_id"]]
    assert revision["external_ref"].endswith(":1:review1") and planning_spend.is_unasked(revision)
    assert "- r1-1 (api): api reads a table store creates. Change: Make api wait for store." in revision["description"]
    assert "It also advised these." in revision["description"]
    assert "--- the plan so far ---" in revision["description"], "it revises: it changes only what it names"
    assert revising["review"]["counts"] == {"found": 2, "fixed": 1, "open": 0, "advice": 1, "dismissed": 0}

    # The revision changes the one task. The reviewer looks again and says its finding is fixed.
    second = _planned(client, events, tasks, goal, [draft("api", ("store",))])
    assert [d["draft_id"] for d in second["plan"]["drafts"]] == ["store", "api"]
    again = _review(client, tasks, events, goal)
    assert again["external_ref"] == f"plan-review:{goal['id']}:2"
    assert "- r1-1: api reads a table store creates." in again["description"], "its earlier findings, by id"
    _ends(
        client,
        tasks,
        events,
        goal,
        again,
        {"verdict": "clean", "summary": "Fixed.", "findings": [], "previous": [{"id": "r1-1", "fixed": True}]},
    )

    done = _plan(client, goal)
    assert (
        done["review"]["rounds"] == 2
        and done["review"]["counts"]["fixed"] == 1
        and done["review"]["counts"]["open"] == 0
    )
    assert done["decision"]["state"] == "ready" and done["decision"]["buttons"][0]["action"] == "approve_plan"
    assert _review(client, tasks, events, goal) is None, "two reviews, and no more by itself"
    assert [e[0] for e in events.events if e[0].startswith("plan_review_")] == [
        "plan_review_started", "plan_review_finished", "plan_review_started", "plan_review_finished",
    ]  # fmt: skip


def test_what_the_review_leaves_open_is_the_operators_to_fix_approve_or_dismiss(world):  # noqa: F811
    client, events, _, tasks = world
    goal = _feature(client, autonomy="ask")
    _planned(client, events, tasks, goal, [draft("store"), draft("api")])
    first = _review(client, tasks, events, goal)
    _ends(client, tasks, events, goal, first, {"verdict": "changes", "summary": "", "findings": [ORDER]})
    _planned(client, events, tasks, goal, [draft("api", ("store",))])
    second = _review(client, tasks, events, goal)
    # The second look says the first finding is not fixed, and finds another that blocks.
    risk = {
        "path": "store",
        "blocking": True,
        "body": "[risk] The migration drops a column the old code reads. Change: Keep the column.",
    }
    _ends(
        client,
        tasks,
        events,
        goal,
        second,
        {"verdict": "changes", "summary": "", "findings": [risk], "previous": [{"id": "r1-1", "fixed": False}]},
    )

    shown = _plan(client, goal)

    assert shown["planning_task_id"] is None, "after the last round the planner is not sent back by itself"
    assert shown["review"]["counts"]["open"] == 2
    decision = shown["decision"]
    assert decision["says"] == "The review found 2 problems, and 2 are still open."
    assert [b["label"] for b in decision["buttons"]][0] == "Fix all 2 (up to $4)"
    assert decision["buttons"][1]["action"] == "approve_plan" and decision["buttons"][1]["label"].startswith(
        "Approve with 2 open"
    )

    # Not a problem: dismissed, with one left, said in full.
    dismissed = client.post(f"/v1/goals/{goal['id']}/plan/review/findings/r1-1/dismiss", headers=OPERATOR)
    assert dismissed.status_code == 200, dismissed.text
    one = dismissed.json()["decision"]
    assert (
        one["says"]
        == "The review found 2 problems, and 1 is still open. The migration drops a column the old code reads."
    )
    assert one["buttons"][0]["label"] == "Fix it (up to $4)"
    assert client.post(f"/v1/goals/{goal['id']}/plan/review/findings/nope/dismiss", headers=OPERATOR).status_code == 404

    # Fix it: the planner is sent back for what is open, asked for by the operator.
    fixing = client.post(f"/v1/goals/{goal['id']}/plan/review/fix", headers=OPERATOR)
    assert fixing.status_code == 200, fixing.text
    sent = tasks.tasks[fixing.json()["planning_task_id"]]
    assert (
        "- r2-1 (store): The migration drops a column the old code reads. Change: Keep the column."
        in sent["description"]
    )
    assert "r1-1" not in sent["description"], "a dismissed finding is not sent back"
    assert not planning_spend.is_unasked(sent), "the operator asked, so the unasked count starts again"
    assert fixing.json()["review"]["counts"]["open"] == 0
    assert client.post(f"/v1/goals/{goal['id']}/plan/review/fix", headers=OPERATOR).status_code == 409


def test_a_review_that_ends_with_nothing_recorded_is_never_a_clean_one(world):  # noqa: F811
    client, events, _, tasks = world
    goal = _feature(client, autonomy="ask")
    _planned(client, events, tasks, goal, [draft("store")])
    task = _review(client, tasks, events, goal)

    collected = _ends(client, tasks, events, goal, task, None, status="failed")

    assert collected["state"] == "failed"
    shown = _plan(client, goal)
    assert shown["review"]["state"] == "failed" and shown["review"]["counts"]["found"] == 0
    assert shown["decision"]["state"] == "ready", "the plan is still the operator's to approve"


def test_a_goal_without_review_gets_none_and_one_can_still_be_asked_for(world):  # noqa: F811
    client, events, _, tasks = world
    goal = _feature(client)
    _planned(client, events, tasks, goal, [draft("store")])

    assert _review(client, tasks, events, goal) is None and _plan(client, goal)["review"] is None

    asked = client.post(f"/v1/goals/{goal['id']}/plan/review", headers=OPERATOR)
    assert asked.status_code == 200, asked.text
    assert asked.json()["review"]["state"] == "running"
    task = next(t for t in tasks.tasks.values() if plan_review.is_review(t))
    assert task["external_ref"].startswith("plan-review-asked:") and not planning_spend.is_unasked(task)
    again = client.post(f"/v1/goals/{goal['id']}/plan/review", headers=OPERATOR)
    assert again.status_code == 409 and "cannot be reviewed now" in again.json()["detail"]


def test_with_no_reviewer_of_another_family_the_plan_is_not_reviewed_and_says_what_to_do(world, monkeypatch):  # noqa: F811
    client, events, _, tasks = world
    goal = _feature(client, autonomy="ask")
    _planned(client, events, tasks, goal, [draft("store")])
    monkeypatch.setattr(plan_review, "reviewers", lambda: ["harness:pi-review/claude:claude-opus-5-5"])

    with pytest.raises(
        plan_review.ReviewError, match="no reviewer of another model family.*Add one to the task policy"
    ):
        _review(client, tasks, events, goal)


def test_the_decision_for_an_open_finding_fits_the_bar():
    goal = {"id": "g", "status": "planning", "plan_task_id": None}
    plan = {"version": 2, "drafts": [draft("a")], "invalid": []}
    long = "x" * 400
    review = {
        "state": "done",
        "version": 2,
        "counts": {"found": 3},
        "findings": [{"status": "open", "severity": "blocking", "says": long}],
    }

    found = actions.plan_decision(
        goal, plan, {"repos": ["o/r"], "may_spend_usd": "9.00", "budget_usd": None}, review=review
    )

    assert found["says"] == "The review found 3 problems, and 1 is still open.", "a long finding stays on its card"
    assert found["buttons"][1]["label"] == "Approve with 1 open"


def test_a_plan_is_marked_while_its_review_runs_so_nothing_asks_for_its_approval(monkeypatch):
    from meta_harness.planner import plan_review

    running = {"state": "running", "version": 2}
    monkeypatch.setattr(plan_review, "rows", lambda goal_id: [{"state": "done", "version": 1}, running])

    assert plan_review.marked("g-1", {"version": 2})["reviewing"] is True
    assert plan_review.marked("g-1", {"version": 3})["reviewing"] is False, "a review of an earlier version"
    assert plan_review.marked("g-1", None) is None
    monkeypatch.setattr(plan_review, "rows", lambda goal_id: [{"state": "done", "version": 2}])
    assert plan_review.marked("g-1", {"version": 2})["reviewing"] is False
