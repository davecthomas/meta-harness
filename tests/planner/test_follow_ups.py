"""A merged pull request's follow-up findings become one issue. No tracker, database or event service is called."""

import asyncio

import pytest

from meta_harness import outbound
from meta_harness.planner import follow_ups
from meta_harness.trackers.base import Issue, TrackerError

PR = "https://github.com/o/r/pull/9"
FINDINGS = [
    {"path": "app.py", "line": 3, "body": "Off by one."},
    {"path": "app.py", "line": 9, "body": "One more case the rule misses."},
    {"path": None, "line": None, "body": "The README does not say this."},
]


def _events(follow: list[int], merged: bool = True) -> list[dict]:
    found = [
        {"position": 1, "event_type": "review_submitted", "payload": {"findings": [FINDINGS[0]]}},
        {"position": 2, "event_type": "pull_request_reviewed", "payload": {"round": 1, "follow_ups": []}},
        {"position": 3, "event_type": "review_submitted", "payload": {"findings": FINDINGS}},
        {"position": 4, "event_type": "pull_request_reviewed", "payload": {"round": 2, "follow_ups": follow}},
    ]
    if merged:
        found.append({"position": 5, "event_type": "pull_request_merged", "payload": {"pr_url": PR}})
    return found


def test_the_follow_ups_are_the_ones_the_last_review_left_on_a_pull_request_that_merged():
    assert follow_ups.left(_events([2, 3])) == (PR, [FINDINGS[1], FINDINGS[2]])
    assert follow_ups.left(_events([])) == (PR, [])
    assert follow_ups.left(_events([2, 3], merged=False)) == ("", []), "nothing is filed for an unmerged one"


def test_the_issue_says_where_the_findings_came_from_and_names_each_ones_file_and_line():
    assert follow_ups.body(PR, [FINDINGS[1], FINDINGS[2]]) == (
        f"The review of {PR} left these findings as follow-ups. They did not block its merge.\n\n"
        "- `app.py` line 9: One more case the rule misses.\n"
        "- The README does not say this."
    )


class Tracker:
    scheme = "github"

    def __init__(self, refuses: bool = False) -> None:
        self.created, self.refuses = [], refuses

    async def create_issue(self, repo, title, body, labels):
        if self.refuses:
            raise TrackerError("403", retryable=False, status=403)
        self.created.append((repo, str(title), str(body), labels))
        return Issue(
            ref=f"github:{repo}#50", repo=repo, number=50, title=str(title), body=str(body), labels=[], state="open"
        )


class Events:
    def __init__(self, found: list[dict]) -> None:
        self.found, self.emitted = found, []

    def read(self, from_position, limit, **filters):
        assert filters["task_id"] == "t-1"
        return {"events": self.found}

    def emit(self, event_type, payload=None, **fields):
        self.emitted.append((event_type, payload))


@pytest.fixture
def world(monkeypatch):
    remembered: list = []
    link = {"ref": "github:o/r#1", "repo": "o/r", "task_id": "t-1", "title": "Fix the bug"}
    monkeypatch.setattr(follow_ups, "_unfiled", lambda goal_id: [link])
    monkeypatch.setattr(follow_ups, "_remember", lambda ref, filed: remembered.append((ref, filed)))
    monkeypatch.setattr(follow_ups, "goal_tracker", lambda tracker, goal: tracker)

    async def passed(text, where, kind, source, provenance=None):
        return outbound.Checked(str(text), outbound.Context(outbound.destination_of(where), kind, source, provenance))

    monkeypatch.setattr(follow_ups.outbound, "for_tracker", passed)
    monkeypatch.setattr(follow_ups.outbound, "writing_outcomes", lambda checked: [])
    monkeypatch.setattr(follow_ups.writing, "record", lambda *a, **k: None)
    return remembered


def test_a_merged_pull_request_with_follow_ups_gets_one_issue_with_no_goal_label_filed_once(world):
    tracker, events, errors = Tracker(), Events(_events([2, 3])), []

    filed = asyncio.run(follow_ups.file({"id": "g-1"}, tracker, events, errors))

    assert filed == ["github:o/r#50"] and errors == [] and world == [("github:o/r#1", "github:o/r#50")]
    ((repo, title, body, labels),) = tracker.created
    assert (repo, title, labels) == ("o/r", "Follow-ups from: Fix the bug", [])
    assert "`app.py` line 9" in body and PR in body
    assert events.emitted[-1] == (
        "follow_ups_filed",
        {"ref": "github:o/r#50", "from_ref": "github:o/r#1", "goal_id": "g-1", "pr_url": PR, "findings": 2},
    )


def test_a_pull_request_with_no_follow_ups_files_nothing_and_is_not_looked_at_again(world):
    tracker = Tracker()

    assert asyncio.run(follow_ups.file({"id": "g-1"}, tracker, Events(_events([])), [])) == []
    assert tracker.created == [] and world == [("github:o/r#1", "")]


def test_a_tracker_that_refuses_is_reported_and_the_next_sweep_tries_again(world):
    errors: list[str] = []

    assert asyncio.run(follow_ups.file({"id": "g-1"}, Tracker(refuses=True), Events(_events([2])), errors)) == []
    assert world == [] and errors[0].startswith("github:o/r#1: its follow-ups could not be filed")
