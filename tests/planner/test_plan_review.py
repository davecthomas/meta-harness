"""Adversarial plan review: a second model tries to find what is wrong with a plan before it is approved."""

import asyncio
from decimal import Decimal

import pytest
from psycopg.types.json import Jsonb

from meta_harness import db
from meta_harness.planner import actions, plan_review, planning, planning_spend, plans

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


@pytest.mark.parametrize("review_max", ["7.5", None])
def test_the_review_button_shows_the_cap_the_review_runs_under(world, monkeypatch, review_max):  # noqa: F811
    client, events, _, tasks = world
    config = dict(planning.settings())
    config.pop("adversarial_review", None)
    if review_max:
        config["adversarial_review"] = {"max_usd": review_max}
    monkeypatch.setattr(planning, "settings", lambda: config)
    goal = _feature(client, autonomy="ask")
    _planned(client, events, tasks, goal, [draft("store")])

    shown = _plan(client, goal)["review_usd"]
    task = _review(client, tasks, events, goal)

    assert Decimal(shown) == Decimal(str(review_max or config["max_usd"]))
    assert shown.count(".") == 1 and len(shown.split(".")[1]) == 2
    assert Decimal(str(task["max_usd"])) == Decimal(shown)


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


def test_cancel_ends_a_running_review_and_keeps_the_findings_it_recorded(world):  # noqa: F811
    client, events, _, tasks = world
    goal = _feature(client, autonomy="ask")
    _planned(client, events, tasks, goal, [draft("store"), draft("api")])
    task = _review(client, tasks, events, goal)
    events.emit(
        "review_submitted",
        {"verdict": "changes", "summary": "One order problem.", "findings": [ORDER]},
        task_id=task["id"],
    )

    row = plan_review.cancel(goal["id"], tasks, events, "dave", "plan edited")

    assert tasks.tasks[task["id"]]["status"] == "canceled"
    assert row["state"] == "canceled" and [f["id"] for f in row["findings"]] == ["r1-1"]
    shown = _plan(client, goal)["review"]
    assert shown["state"] == "canceled" and shown["summary"] == "One order problem."
    assert [f["id"] for f in shown["findings"]] == ["r1-1"]
    assert events.events[-1][0] == "plan_review_finished"
    assert plan_review.cancel(goal["id"], tasks, events, "dave", "again") is None


def test_cancel_with_no_running_review_returns_none_and_cancels_nothing(world):  # noqa: F811
    client, events, _, tasks = world
    goal = _feature(client, autonomy="ask")
    _planned(client, events, tasks, goal, [draft("store")])
    task = _review(client, tasks, events, goal)
    _ends(client, tasks, events, goal, task, {"verdict": "clean", "summary": "", "findings": []})

    assert plan_review.cancel(goal["id"], tasks, events, "dave", "plan edited") is None
    assert tasks.tasks[task["id"]]["status"] == "closed"


def test_a_review_canceled_before_it_submitted_anything_reads_canceled_with_no_findings(world):  # noqa: F811
    client, events, _, tasks = world
    goal = _feature(client, autonomy="ask")
    _planned(client, events, tasks, goal, [draft("store")])
    task = _review(client, tasks, events, goal)

    collected = _ends(client, tasks, events, goal, task, None, status="canceled")

    assert collected["state"] == "canceled"
    shown = _plan(client, goal)["review"]
    assert shown["state"] == "canceled" and shown["findings"] == []


def test_collect_reads_a_canceled_review_with_findings_as_canceled_and_does_not_revise(world):  # noqa: F811
    client, events, _, tasks = world
    goal = _feature(client, autonomy="ask")
    _planned(client, events, tasks, goal, [draft("store"), draft("api")])
    task = _review(client, tasks, events, goal)

    collected = _ends(
        client, tasks, events, goal, task, {"verdict": "changes", "summary": "", "findings": [ORDER]}, "canceled"
    )

    assert collected["state"] == "canceled" and len(collected["findings"]) == 1
    assert _plan(client, goal)["planning_task_id"] is None


def test_approving_while_a_review_runs_cancels_it_keeps_its_findings_and_approves(world):  # noqa: F811
    client, events, _, tasks = world
    goal = _feature(client, autonomy="ask")
    _planned(client, events, tasks, goal, [draft("store"), draft("api")])
    task = _review(client, tasks, events, goal)
    events.emit(
        "review_submitted",
        {"verdict": "changes", "summary": "One order problem.", "findings": [ORDER]},
        task_id=task["id"],
    )

    approved = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR)

    assert approved.status_code == 200, approved.text
    assert tasks.tasks[task["id"]]["status"] == "canceled"
    assert _goal(client, goal)["status"] == "active"
    [review] = plan_review.rows(goal["id"])
    assert review["state"] == "canceled" and [f["id"] for f in review["findings"]] == ["r1-1"]


def test_approving_goes_on_when_the_review_cannot_be_canceled(world, monkeypatch):  # noqa: F811
    client, events, _, tasks = world
    goal = _feature(client, autonomy="ask")
    _planned(client, events, tasks, goal, [draft("store")])
    _review(client, tasks, events, goal)

    def broken(*args, **kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(plan_review, "cancel", broken)

    approved = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR)

    assert approved.status_code == 200, approved.text
    assert _goal(client, goal)["status"] == "active"


def test_saving_an_edit_while_a_review_runs_cancels_it_and_offers_another(world):  # noqa: F811
    client, events, _, tasks = world
    goal = _feature(client, autonomy="ask")
    plan = _planned(client, events, tasks, goal, [draft("store"), draft("api")])["plan"]
    task = _review(client, tasks, events, goal)

    edited = client.put(f"/v1/goals/{goal['id']}/plan", json={"drafts": plan["drafts"][:1]}, headers=OPERATOR)

    assert edited.status_code == 200, edited.text
    assert tasks.tasks[task["id"]]["status"] == "canceled"
    shown = edited.json()
    assert shown["review"]["state"] == "canceled" and shown["plan"]["version"] == 2
    again = [b for b in shown["decision"]["buttons"] if b["action"] == "review_plan"]
    assert [b["label"] for b in again] == [f"Review again (up to {actions._usd(shown['review_usd'])})"]
    assert again[0]["label"].startswith("Review again (up to $")


def test_planning_again_while_a_review_runs_leaves_it_running(world):  # noqa: F811
    client, events, _, tasks = world
    goal = _feature(client, autonomy="ask")
    _planned(client, events, tasks, goal, [draft("store")])
    task = _review(client, tasks, events, goal)
    before = tasks.tasks[task["id"]]["status"]

    replanned = client.post(f"/v1/goals/{goal['id']}/replan", json={"note": "Smaller tasks."}, headers=OPERATOR)

    assert replanned.status_code == 202, replanned.text
    assert tasks.tasks[task["id"]]["status"] == before != "canceled"
    assert plan_review.rows(goal["id"])[-1]["state"] == "running"
    assert replanned.json()["review"]["state"] == "running"


def test_an_approval_refused_leaves_the_running_review_alone(world):  # noqa: F811
    """A second press while an approval runs, and a plan that cannot be approved, used to end the review."""
    client, events, _, tasks = world
    goal = _feature(client, autonomy="ask")
    _planned(client, events, tasks, goal, [draft("store")])
    task = _review(client, tasks, events, goal)

    with db.connect(autocommit=True) as conn:
        assert plans.claim_approval(conn, goal["id"]), "another approval holds the plan"
    second = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR)
    assert second.status_code == 409 and "being approved already" in second.json()["detail"]
    assert tasks.tasks[task["id"]]["status"] != "canceled"
    assert plan_review.rows(goal["id"])[-1]["state"] == "running"

    with db.connect(autocommit=True) as conn:
        plans.end_approval(conn, goal["id"], None)
        conn.execute(
            "UPDATE planner.plan SET invalid = %s WHERE goal_id = %s", (Jsonb(["store names no repo"]), goal["id"])
        )
    invalid = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR)
    assert invalid.status_code == 409, invalid.text
    assert tasks.tasks[task["id"]]["status"] != "canceled"
    assert plan_review.rows(goal["id"])[-1]["state"] == "running"


def test_a_version_just_saved_asks_nothing_while_its_review_is_about_to_start(world):  # noqa: F811
    """A review starts at the sweep after a version is saved. For that moment the plan read as ready: it showed
    its open question and Approve, and took both back when the review began."""
    from datetime import UTC, datetime, timedelta

    from meta_harness.planner import needs

    client, events, _, tasks = world
    goal = _feature(client, autonomy="ask")
    shown = _planned(client, events, tasks, goal, [draft("store")])
    goal = _goal(client, goal)

    # Saved, reviews on, and no review of this version yet: one is due.
    with db.connect(autocommit=True) as conn:
        plan = plans.latest(conn, goal["id"])
    assert plan_review.due(goal, plan) is True
    assert shown["decision"]["state"] == "improving" and shown["decision"]["buttons"] == []
    assert "about to check this version" in shown["decision"]["says"]
    assert shown["decision"]["link"] == {"action": "approve_plan", "label": "Approve now"}
    assert [n["action"] for n in needs.by_id(goal["id"], tasks)[1]] == [], "nothing is asked of the operator"

    # A review that cannot start holds no plan for long: past the wait, the plan reads as ready.
    later = datetime.now(UTC) + timedelta(seconds=plan_review.DUE_SECONDS + 1)
    assert plan_review.due(goal, plan, later) is False

    # Once it runs, the running review is what the plan says, and once it has run this version, nothing is due.
    task = _review(client, tasks, events, goal)
    assert plan_review.due(goal, plan) is False
    assert "is checking the plan" in _plan(client, goal)["decision"]["says"]
    _ends(client, tasks, events, goal, task, {"verdict": "clean", "summary": "", "findings": []})
    ready = _plan(client, goal)
    assert ready["decision"]["state"] == "ready" and plan_review.due(_goal(client, goal), plan) is False


def test_a_goal_with_reviews_off_is_ready_as_soon_as_its_plan_is_saved(world):  # noqa: F811
    client, events, _, tasks = world
    goal = _feature(client, autonomy="full")
    shown = _planned(client, events, tasks, goal, [draft("store")])

    assert _goal(client, goal)["adversarial_review"] is False
    assert shown["decision"]["state"] == "ready"
