"""When a sweep moves a goal to met, with the store replaced: these run without Postgres."""

import pytest

from meta_harness.planner import sweep
from meta_harness.planner.tracker import Issue

GOAL = "g-test"
REPO = "davecthomas/meta-harness"


class Events:
    def __init__(self):
        self.events = []

    def emit(self, event_type, payload=None, **fields):
        self.events.append((event_type, payload or {}))
        return "id"


class Tracker:
    def __init__(self, open_issues=()):
        self.open = list(open_issues)

    async def list_open(self, repo, label):
        return self.open


class Tasks:
    def __init__(self, unfinished=()):
        self.unfinished = list(unfinished)

    def list(self, statuses=None, goal_id=None, limit=100):
        assert goal_id == GOAL and "running" in statuses and "closed" not in statuses
        return self.unfinished[:limit]


def _issue(number: int) -> Issue:
    ref = f"github:{REPO}#{number}"
    return Issue(ref=ref, repo=REPO, number=number, title="t", body="", labels=["l"], state="open")


@pytest.fixture
def store(monkeypatch):
    """The planner store as one goal's links and the status updates asked of it."""
    state = {"links": [], "others": {}, "updates": []}

    def complete(goal_id):
        state["updates"].append(goal_id)
        return {"id": goal_id, "status": "met", "closed_at": "2026-01-01T00:00:00+00:00"}, {"status": "active"}

    monkeypatch.setattr(sweep, "_links", lambda goal_id: state["links"])
    monkeypatch.setattr(sweep, "_link", lambda ref: state["others"].get(ref))
    monkeypatch.setattr(sweep, "_complete", complete)
    return state


def _goal(status="active") -> dict:
    return {"id": GOAL, "status": status, "repos": [REPO], "label": "l"}


async def _check(goal, tracker=None, tasks=None):
    events, result = Events(), sweep.SweepResult(goal["id"])
    await sweep._complete_if_met(goal, tracker or Tracker(), tasks or Tasks(), events, result)
    return result, events


async def test_a_goal_with_every_issue_closed_and_no_work_left_is_met(store):
    store["links"] = [{"ref": "a", "state": "closed"}, {"ref": "b", "state": "closed"}]
    goal = _goal()

    result, events = await _check(goal)

    assert result.met and goal["status"] == "met" and store["updates"] == [GOAL]
    assert [t for t, _ in events.events] == ["goal_changed", "goal_met"]
    assert events.events[0][1]["changes"] == {"status": {"from": "active", "to": "met"}}


async def test_a_goal_that_never_held_an_issue_is_not_met(store):
    result, _ = await _check(_goal())
    assert not result.met and store["updates"] == []


async def test_an_open_issue_keeps_the_goal_active(store):
    store["links"] = [{"ref": "a", "state": "closed"}, {"ref": "b", "state": "open"}]
    result, _ = await _check(_goal())
    assert not result.met and store["updates"] == []


async def test_a_task_with_work_left_keeps_the_goal_active(store):
    store["links"] = [{"ref": "a", "state": "closed"}]
    result, _ = await _check(_goal(), tasks=Tasks([{"id": "t-1", "status": "ready"}]))
    assert not result.met and store["updates"] == []


async def test_a_new_open_issue_under_the_label_keeps_the_goal_active(store):
    store["links"] = [{"ref": "a", "state": "closed"}]
    result, _ = await _check(_goal(), tracker=Tracker([_issue(5)]))
    assert not result.met and store["updates"] == []


async def test_an_open_issue_another_goal_holds_does_not_keep_this_one_open(store):
    store["links"] = [{"ref": "a", "state": "closed"}]
    held = _issue(6)
    store["others"][held.ref] = {"ref": held.ref, "goal_id": "g-other"}
    result, _ = await _check(_goal(), tracker=Tracker([held]))
    assert result.met


@pytest.mark.parametrize("status", ["paused", "stalled", "met", "canceled"])
async def test_only_an_active_goal_is_checked(store, status):
    store["links"] = [{"ref": "a", "state": "closed"}]
    result, _ = await _check(_goal(status))
    assert not result.met and store["updates"] == []
