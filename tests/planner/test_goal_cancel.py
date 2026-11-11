"""Canceling a goal ends the tasks it has not started, and a goal no sweep covers reads its issues' states
from the tracker when its page asks for them."""

import pytest
from fastapi.testclient import TestClient

from meta_harness.planner import sweep
from meta_harness.planner.service import create_app

from .test_planner import (  # noqa: F401 - the fixtures these tests run on
    OPERATOR,
    REPO,
    Events,
    FakeTasks,
    FakeTracker,
    _goal,
    _label,
    _no_title,
    _number,
    _sweep,
    migrated,
    registry,
)

pytestmark = pytest.mark.db


class Tasks(FakeTasks):
    """A task service that cancels as the real one does: a running task is only asked to stop."""

    def __init__(self):
        super().__init__()
        self.cancels: list[tuple[str, str, str | None]] = []

    def cancel(self, task_id, reason="", actor=None):
        self.cancels.append((task_id, reason, actor))
        task = self.tasks[task_id]
        if task["status"] == "running":
            task["cancel_requested"] = True
        else:
            task["status"] = "canceled"
        return task


@pytest.fixture
def parts():
    return Events(), FakeTracker(), Tasks()


@pytest.fixture
def api(parts):
    events, tracker, tasks = parts
    app = create_app(
        events, tracker, tasks, operator_keys={"op-key": "dave"}, service_keys={}, sweep_seconds=0, titler=_no_title
    )
    with TestClient(app) as client:
        yield client


def _tasks_by_ref(tasks) -> dict[str, dict]:
    return {task["external_ref"]: task for task in tasks.tasks.values()}


def test_canceling_a_goal_cancels_its_unstarted_tasks_and_leaves_one_with_a_pull_request(api, parts):
    _, tracker, tasks = parts
    label = _label()
    goal = _goal(api, label)
    first = _number()
    queued = tracker.add(REPO, first, labels=[label])
    blocked = tracker.add(REPO, _number(), body=f"Depends on: #{first}\n", labels=[label])
    running = tracker.add(REPO, _number(), labels=[label])
    published = tracker.add(REPO, _number(), labels=[label])
    ready = tracker.add(REPO, _number(), labels=[label])
    _sweep(api, goal)
    by_ref = _tasks_by_ref(tasks)
    assert by_ref[blocked]["status"] == "blocked"
    by_ref[running]["status"] = "running"
    by_ref[published].update(status="running", pr_url=f"https://github.com/{REPO}/pull/1")
    by_ref[ready].update(status="ready", pr_url=f"https://github.com/{REPO}/pull/2")

    canceled = api.post(f"/v1/goals/{goal['id']}/cancel", headers=OPERATOR).json()

    assert canceled["status"] == "canceled"
    assert by_ref[queued]["status"] == "canceled" and by_ref[blocked]["status"] == "canceled"
    assert by_ref[running]["status"] == "running" and by_ref[running]["cancel_requested"], "asked to stop"
    assert by_ref[published]["status"] == "running" and not by_ref[published].get("cancel_requested")
    assert by_ref[ready]["status"] == "ready"
    assert sorted(canceled["tasks"]["canceled"]) == sorted([by_ref[queued]["id"], by_ref[blocked]["id"]])
    assert canceled["tasks"]["stopping"] == [by_ref[running]["id"]]
    assert sorted(canceled["tasks"]["left"]) == sorted([by_ref[published]["id"], by_ref[ready]["id"]])
    assert canceled["tasks"]["errors"] == []
    assert {(reason, actor) for _, reason, actor in tasks.cancels} == {("its goal was canceled", "dave")}


def test_pausing_a_goal_cancels_nothing(api, parts):
    _, tracker, tasks = parts
    label = _label()
    goal = _goal(api, label)
    ref = tracker.add(REPO, _number(), labels=[label])
    _sweep(api, goal)

    assert api.post(f"/v1/goals/{goal['id']}/pause", headers=OPERATOR).json()["status"] == "paused"

    assert _tasks_by_ref(tasks)[ref]["status"] == "queued" and tasks.cancels == []


def test_reopening_a_canceled_goal_does_not_bring_its_canceled_tasks_back(api, parts):
    _, tracker, tasks = parts
    label = _label()
    goal = _goal(api, label)
    ref = tracker.add(REPO, _number(), labels=[label])
    _sweep(api, goal)
    api.post(f"/v1/goals/{goal['id']}/cancel", headers=OPERATOR)

    assert api.post(f"/v1/goals/{goal['id']}/reopen", headers=OPERATOR).json()["status"] == "active"
    _sweep(api, goal)

    assert _tasks_by_ref(tasks)[ref]["status"] == "canceled"


def test_a_canceled_goals_page_reads_its_issues_states_from_the_tracker(api, parts):
    events, tracker, tasks = parts
    label = _label()
    goal = _goal(api, label)
    ref = tracker.add(REPO, _number(), labels=[label])
    _sweep(api, goal)
    swept = api.get(f"/v1/goals/{goal['id']}/issues", headers=OPERATOR).json()
    assert swept["goal_status"] == "active" and swept["states_checked_at"] is None, "its sweep keeps the states"
    api.post(f"/v1/goals/{goal['id']}/cancel", headers=OPERATOR)
    # The issue closes after the goal's last sweep.
    tracker.issues[ref].state = "closed"

    shown = api.get(f"/v1/goals/{goal['id']}/issues", headers=OPERATOR).json()

    assert shown["goal_status"] == "canceled" and shown["states_checked_at"] and shown["states_complete"]
    assert {i["ref"]: i["state"] for i in shown["issues"]} == {ref: "closed"}
    assert ("issue_closed", {"ref": ref}) in events.events
    goal_read = api.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()
    assert goal_read["issues"][0]["state"] == "closed" and goal_read["states_checked_at"]


def test_an_issue_that_cannot_be_read_keeps_its_state_and_the_page_says_so(api, parts):
    _, tracker, _ = parts
    label = _label()
    goal = _goal(api, label)
    ref = tracker.add(REPO, _number(), labels=[label])
    _sweep(api, goal)
    api.post(f"/v1/goals/{goal['id']}/cancel", headers=OPERATOR)

    async def unreadable(ref):
        raise ValueError("no such issue")

    tracker.read = unreadable

    shown = api.get(f"/v1/goals/{goal['id']}/issues", headers=OPERATOR).json()

    assert shown["states_complete"] is False
    assert {i["ref"]: i["state"] for i in shown["issues"]} == {ref: "open"}


async def test_a_follow_up_records_an_issue_that_closed_after_the_goal_ended(api, parts):
    events, tracker, tasks = parts
    label = _label()
    goal = _goal(api, label)
    ref = tracker.add(REPO, _number(), labels=[label])
    _sweep(api, goal)
    paused = api.post(f"/v1/goals/{goal['id']}/pause", headers=OPERATOR).json()
    tracker.issues[ref].state = "closed"

    result = await sweep.follow_up_goal(paused, tracker, tasks, events)

    assert result.closed == [ref] and sweep._link(ref)["state"] == "closed"
    assert _tasks_by_ref(tasks)[ref]["status"] == "queued", "a follow-up changes no task"
