"""The plan as a person deciding on it needs it: its state, its one decision, a few figures, and its versions."""

from meta_harness.planner import actions, plan_view

from .test_planner import OPERATOR, migrated, registry  # noqa: F401
from .test_planning import REPO, _feature, _planned, draft
from .test_planning import world  # noqa: F401

GOAL = {"id": "g-1", "status": "planning", "plan_task_id": None, "max_usd": "100.00"}
BY_SIZE = {"small": "3.00", "medium": "8.00", "large": "20.00"}


def _plan(**fields) -> dict:
    return {
        "drafts": [draft("store"), draft("api", ("store",))],
        "invalid": [],
        "gaps": [],
        "stopped_at": None,
        **fields,
    }


def test_the_plans_figures_are_an_upper_bound_from_each_tasks_size():
    sized = [{**draft("store"), "size": "small"}, {**draft("api", ("store",)), "size": "medium"}, draft("ui")]

    found = plan_view.glance(GOAL, _plan(drafts=sized, gaps=["A question."]), BY_SIZE, parallel=1)

    assert found["tasks"] == 3 and found["repos"] == [REPO]
    assert found["may_spend_usd"] == "31.00", "a task not sized yet counts at the largest size's limit"
    assert found["budget_usd"] == "100.00" and found["unsized"] == 1
    assert found["can_start"] == 1, "two wait for nothing, and the goal runs one at a time"
    assert found["open_questions"] == 1


def test_a_version_is_compared_with_the_last_an_operator_made_else_the_planners_first():
    row = lambda v, source, by: {"version": v, "source": source, "created_by": by}  # noqa: E731

    assert plan_view.baseline([row(1, "planned", "planner")]) is None
    assert plan_view.baseline([row(1, "planned", "planner"), row(2, "suggested", "planner")]) == 1
    assert (
        plan_view.baseline([row(1, "planned", "planner"), row(2, "planned", "planner"), row(3, "suggested", "planner")])
        == 1
    )
    edited = [row(1, "planned", "planner"), row(2, "edited", "dave")]
    assert plan_view.baseline(edited) is None, "a version an operator made is what the next is compared with"
    assert plan_view.baseline([*edited, row(3, "planned", "planner")]) == 2


def test_a_plan_ready_to_approve_asks_one_thing_and_says_what_it_may_spend():
    glance = plan_view.glance(GOAL, _plan(), BY_SIZE, parallel=None)

    found = actions.plan_decision(GOAL, _plan(), glance, planning_usd="4.00")

    assert found["state"] == "ready"
    assert found["says"] == "2 tasks in 1 repo. Running them may spend up to $40 of your $100 budget."
    assert found["buttons"] == [
        {"action": "approve_plan", "label": "Approve and start (up to $40)"},
        {"action": "replan", "label": "Plan again (up to $4)"},
    ]
    # A budget is a hard cap, and the plan says when it asks for more.
    tight = actions.plan_decision({**GOAL, "max_usd": "30.00"}, _plan(), {**glance, "budget_usd": "30.00"})
    assert tight["says"].endswith("All of them may need up to $40, and your $30 budget stops work before that.")
    assert [b["label"] for b in tight["buttons"]] == [
        "Approve and start (up to $30)",
        "Raise the budget",
        "Plan again",
    ], "the button names what approving can spend, and raising the budget is offered beside it"


def test_each_state_of_a_plan_has_its_sentence_and_its_buttons_the_recommended_one_first():
    said = lambda goal, plan, **more: actions.plan_decision(goal, plan, planning_usd="4.00", **more)  # noqa: E731

    assert said({**GOAL, "plan_task_id": "t-1"}, None) == {
        "state": "drafting", "says": "The planner is writing the plan.", "buttons": []
    }  # fmt: skip
    improving = said({**GOAL, "plan_task_id": "t-2"}, _plan())
    assert improving["state"] == "improving" and improving["buttons"] == [], "nothing is asked while it works"
    stopped = said(GOAL, _plan(stopped_at="turns", invalid=["planning stopped at its turns limit"]))
    assert stopped["says"] == "Planning stopped at its turns limit with 2 tasks drafted."
    assert stopped["buttons"] == [{"action": "replan", "label": "Carry on planning (up to $4)"}]
    blocked = said(GOAL, _plan(invalid=["the task api is too large for one pull request.", "another"]))
    assert blocked["state"] == "cannot_approve"
    assert blocked["says"] == (
        "This plan cannot be approved yet: the task api is too large for one pull request. And 1 more."
    )
    assert [b["action"] for b in blocked["buttons"]] == ["replan"]
    waiting = said(GOAL, _plan(), suggestion={"fixes": [{"gap": "g", "change": "c"}]})
    assert waiting["says"] == "The planner drafted a fix for 1 gap in this plan."
    assert [b["label"] for b in waiting["buttons"]] == ["Accept the fix", "Keep the plan as it is"]
    approved = said({**GOAL, "status": "active"}, _plan())
    assert approved["state"] == "approved" and approved["buttons"] == []
    # Every sentence fits the decision bar: two sentences at most, and no list.
    for found in (improving, stopped, blocked, waiting, approved):
        assert found["says"].count(". ") <= 1 and len(found["says"]) <= 280, found["says"]


def test_the_plan_api_gives_the_decision_the_figures_the_changes_and_the_versions(world):  # noqa: F811
    client, events, _, tasks = world
    goal = _feature(client)
    first = _planned(client, events, tasks, goal, [draft("store"), draft("api")])

    assert first["decision"]["state"] == "ready" and first["changes"] is None, "the first version changes nothing"
    assert first["glance"]["tasks"] == 2 and first["glance"]["repos"] == [REPO]
    assert [b["action"] for b in first["decision"]["buttons"]] == ["approve_plan", "replan"]

    client.post(f"/v1/goals/{goal['id']}/replan", json={"note": "Add tests."}, headers=OPERATOR)
    working = client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()
    assert working["decision"]["state"] == "improving" and working["decision"]["buttons"] == []
    second = _planned(client, events, tasks, goal, [draft("tests", ("api",))])

    assert second["changes"]["from_version"] == 1 and second["changes"]["lines"] == ["1 task added"]
    assert second["changes"]["diff"]["added"] == [{"id": "tests", "title": "Build tests"}]
    history = client.get(f"/v1/goals/{goal['id']}/plan/versions", headers=OPERATOR).json()["versions"]
    assert [(v["version"], v["source"], v["tasks"]) for v in history] == [(1, "planned", 2), (2, "planned", 3)]
