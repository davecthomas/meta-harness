"""A goal's standing, from where its tasks stand. These run without Postgres."""

from meta_harness.supervisor.goals import STATES, goal_standing


def _task(task_id: str, status: str, owner: str | None, situation: str = "x", since: str = "2026-11-12T10:00:00+00:00"):
    standing = (
        None
        if owner is None
        else {
            "owner": owner,
            "situation": situation,
            "reason": f"why {task_id}",
            "ask": f"do {task_id}",
            "since": since,
        }
    )
    return {"id": task_id, "title": f"Task {task_id}", "status": status, "standing": standing}


def test_an_active_goal_with_a_task_being_worked_is_moving_even_with_others_waiting_on_an_operator():
    found = goal_standing("active", [_task("a", "running", "workflow"), _task("b", "failed", "operator")])
    assert (found["state"], found["moving"]) == ("moving", 1)
    assert [y["task_id"] for y in found["yours"]] == ["b"], "what waits on the operator is still listed"


def test_an_active_goal_with_nothing_moving_and_work_left_waits_on_the_operator_and_says_for_what():
    tasks = [
        _task("late", "failed", "operator", since="2026-11-12T12:00:00+00:00"),
        _task("early", "ready", "operator", since="2026-11-12T09:00:00+00:00"),
        _task("done", "closed", None),
        _task("gone", "canceled", "planner"),
    ]

    found = goal_standing("active", tasks, refused=1)

    assert found["state"] == "needs_you"
    assert found["reason"] == "nothing in it is moving: 2 tasks wait on you and 1 issue was refused"
    assert [(y["task_id"], y["ask"]) for y in found["yours"]] == [("early", "do early"), ("late", "do late")]
    assert goal_standing("active", [], refused=2)["state"] == "needs_you"


def test_a_goal_that_is_not_running_is_held_and_says_how_to_start_it_and_an_ended_one_is_done():
    assert goal_standing("paused", [_task("a", "queued", "operator", "goal_not_active")]) == {
        "state": "held",
        "reason": "it is paused",
        "ask": "Resume the goal to let its tasks start.",
        "yours": [],
        "moving": 0,
    }
    assert "Raise its budget" in goal_standing("budget_exhausted", [])["ask"]
    assert "Approve its plan" in goal_standing("planning", [])["ask"]
    for status in ("met", "canceled"):
        assert goal_standing(status, [_task("a", "failed", "operator")])["state"] == "done"


def test_a_goal_with_nothing_left_is_idle_and_a_task_not_looked_at_yet_counts_as_moving():
    assert goal_standing("active", [_task("a", "closed", None)])["state"] == "idle"
    assert goal_standing("active", [_task("new", "queued", None)])["state"] == "moving"
    assert all(goal_standing(s, [])["state"] in STATES for s in ("active", "paused", "planning", "stalled", "met"))
