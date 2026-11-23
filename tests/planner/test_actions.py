"""What a goal needs from the operator now, read from its records alone, as the actions a page offers."""

from meta_harness.planner import actions

PR = "https://github.com/o/r/pull/7"


def _kinds(goal: dict, links: list[dict], tasks: list[dict], **more) -> list[tuple[str, str]]:
    return [
        (a["action"], a["ref"]) for a in actions.for_goal({"id": "g-1", "label": "mh:x", **goal}, links, tasks, **more)
    ]


def test_each_task_and_issue_that_waits_on_the_operator_gets_the_action_its_state_calls_for():
    links = [
        {"ref": "mh:i-1", "state": "open", "task_id": "t-1"},
        {"ref": "mh:i-2", "state": "open", "task_id": "t-2"},
        {"ref": "mh:i-3", "state": "open", "task_id": "t-3"},
        {"ref": "mh:i-4", "state": "open", "task_id": "t-4"},
        {"ref": "mh:i-5", "state": "open", "task_id": None, "held_reason": "sized large: 4 areas"},
        {"ref": "mh:i-6", "state": "closed", "task_id": "t-6"},
    ]
    tasks = [
        {"id": "t-1", "status": "ready", "pr_url": PR},
        {"id": "t-2", "status": "failed", "pr_url": None},
        {"id": "t-3", "status": "running", "pr_url": None},
        {"id": "t-4", "status": "review_unresolved", "pr_url": PR},
        {"id": "t-6", "status": "failed", "pr_url": None},
    ]
    active = {"status": "active", "auto_merge": False}

    found = actions.for_goal({"id": "g-1", "label": "mh:x", **active}, links, tasks)

    assert [(a["action"], a["ref"], a["task_id"]) for a in found] == [
        ("merge", "mh:i-1", "t-1"),
        ("retry", "mh:i-2", "t-2"),
        ("settle_review", "mh:i-4", "t-4"),
    ], "a running task and a closed issue need nothing, and no more than three are given"
    assert all(a["goal_id"] == "g-1" and a["label"] == "mh:x" for a in found)
    # With fewer ahead of it, the held issue is offered to be split or run.
    assert _kinds(active, links[4:], []) == [("split_or_run", "mh:i-5")]
    # A goal that merges by itself asks nobody to merge.
    assert _kinds({**active, "auto_merge": True}, links[:1], tasks[:1]) == []


def test_a_plan_is_offered_for_approval_only_when_it_can_be_approved():
    planning = {"status": "planning", "plan_task_id": None}

    assert _kinds(planning, [], [], plan={"version": 1, "invalid": []}) == [("approve_plan", "")]
    assert _kinds(planning, [], [], plan={"version": 1, "invalid": ["too large"]}) == [("fix_plan", "")]
    assert _kinds(planning, [], [], plan=None) == [], "no plan yet: the planner is drafting"
    resizing = {**planning, "plan_task_id": "t-9"}
    assert _kinds(resizing, [], [], plan={"version": 1, "invalid": ["too large"]}) == []
    fix = {"fixes": ["a gap"], "changes": ["Adds: a task"]}
    assert [k for k, _ in _kinds(planning, [], [], plan={"version": 1, "invalid": []}, suggestion=fix)] == [
        "accept_fix",
        "approve_plan",
    ]
    assert _kinds({"status": "budget_exhausted"}, [], []) == [("raise_budget", "")]


def test_a_suggested_step_gets_the_goals_own_needs_of_its_kind_or_the_way_to_the_goal():
    goal = {"id": "g-1", "label": "mh:x", "status": "active"}
    needs = [
        {"action": "merge", "ref": "mh:i-1", "task_id": "t-1"},
        {"action": "retry", "ref": "mh:i-2", "task_id": "t-2"},
        {"action": "merge", "ref": "mh:i-3", "task_id": "t-3"},
        {"action": "merge", "ref": "mh:i-4", "task_id": "t-4"},
    ]

    def kinds(wanted: str, about: dict = goal, given: list[dict] = needs) -> list[tuple[str, str]]:
        return [(b["action"], b.get("says") or b["ref"]) for b in actions.suggested(wanted, about, given)]

    assert kinds("merge") == [("merge", "mh:i-1"), ("merge", "mh:i-3")], "two at most"
    assert kinds("open_task") == [("merge", "mh:i-1"), ("retry", "mh:i-2")]
    assert kinds("raise_budget") == [("open_goal", "goal")], "the records hold no such need: open the goal"
    assert kinds("open_plan") == [("open_goal", "goal")], "an active goal's plan is approved already"
    assert kinds("open_plan", {**goal, "status": "planning"}, []) == [("open_goal", "plan")]
    assert kinds("next_steps", {**goal, "status": "met"}, []) == [("open_goal", "next_steps")]
    assert kinds("next_steps") == [("open_goal", "goal")], "a running goal has not left anything for later yet"
