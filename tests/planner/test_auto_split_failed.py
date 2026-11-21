"""The planner splits an issue whose task ran out of turns on the strongest model, and closes that task."""

import asyncio

import pytest

from meta_harness.planner import auto_split

LINK = {"ref": "jira:ABC-1", "state": "open", "task_id": "t-1", "split_into": None, "dropped": False}
FAILED = {
    "id": "t-1",
    "status": "failed",
    "pr_url": None,
    "complexity": None,
    "status_reason": "run 1 on harness:pi-careful/claude:opus ended for good: ended ERROR (max_turns): limit",
}


class Tasks:
    def __init__(self, task: dict) -> None:
        self.task, self.set = task, []

    def list(self, statuses, goal_id, limit):
        assert statuses == ["failed"]
        return [self.task]

    def set_status(self, task_id, status, reason=None):
        self.set.append((task_id, status, reason))
        return self.task


class Events:
    def __init__(self, tier: str) -> None:
        self.tier = tier

    def read(self, from_position=0, limit=500, **filters):
        return {"events": [{"payload": {"tier": self.tier}}]}

    def emit(self, *args, **kwargs):
        pass


@pytest.fixture
def world(monkeypatch):
    split: list = []
    recorded: list = []
    monkeypatch.setattr(auto_split, "_held_large", lambda goal_id: [])
    monkeypatch.setattr(auto_split.store, "links", lambda conn, goal_id: [LINK])
    monkeypatch.setattr(auto_split.db, "connect", lambda **kw: __import__("contextlib").nullcontext(None))
    monkeypatch.setattr(auto_split.ledger, "tries_for_issue", lambda ref, remedy: 0)
    monkeypatch.setattr(auto_split.ledger, "write", recorded.append)

    class Tracker:
        async def read(self, ref):
            return object()

    async def draft(issue, titles):
        return [{"title": "Part one", "body": "b"}, {"title": "Part two", "body": "b"}], "claude:m", "0.01"

    async def do_split(goal, link, parts, tracker, events, who, failed_task=None):
        split.append((link["ref"], failed_task))
        return ["jira:ABC-2", "jira:ABC-3"]

    monkeypatch.setattr(auto_split.splits, "draft", draft)
    monkeypatch.setattr(auto_split, "split_issue", do_split)
    monkeypatch.setattr(auto_split, "goal_tracker", lambda tracker, goal: Tracker())
    return split, recorded


def test_an_issue_whose_task_ran_out_of_turns_on_the_strongest_tier_is_split_and_its_task_closed(world):
    split, recorded = world
    tasks = Tasks(FAILED)

    done = asyncio.run(auto_split.run({"id": "g-1", "status": "active"}, None, Events("complex"), tasks))

    assert done == ["jira:ABC-1"] and split == [("jira:ABC-1", "t-1")]
    assert tasks.set == [("t-1", "closed", "its issue was split into ABC-2, ABC-3, which run in its place")]
    assert recorded[0]["situation"] == "failed_too_large" and recorded[0]["outcome"] == "done"


@pytest.mark.parametrize(
    ("task", "tier"),
    [
        (FAILED, "standard"),
        ({**FAILED, "pr_url": "https://github.com/o/r/pull/9"}, "complex"),
        ({**FAILED, "status_reason": "ended ERROR (spend_limit)"}, "complex"),
    ],
)
def test_a_task_a_stronger_model_may_still_fix_or_with_a_pull_request_or_another_failure_is_not_split(
    world, task, tier
):
    split, _ = world

    assert asyncio.run(auto_split.run({"id": "g-1", "status": "active"}, None, Events(tier), Tasks(task))) == []
    assert split == []
