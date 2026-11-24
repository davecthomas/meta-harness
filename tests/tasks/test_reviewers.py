"""The Reviewers table: how each reviewer's findings turned out over a time range (tasks/reviewers.py)."""

from datetime import datetime, timedelta, timezone

from meta_harness.tasks import reviewers

OPUS = "harness:pi-review/claude:claude-opus-5-5"
SOL = "harness:pi-review/openai-responses:gpt-6-sol"
NOW = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)


def _events(*specs: tuple) -> list[dict]:
    """Events from (task, type, payload, days ago), numbered in order as the event service would."""
    return [
        {
            "task_id": task,
            "event_type": kind,
            "payload": payload,
            "position": n,
            "time_utc": (NOW - timedelta(days=ago)).isoformat(),
            "actor": "dave" if kind == "finding_dismissed" else None,
        }
        for n, (task, kind, payload, ago) in enumerate(specs, 1)
    ]


def _review(task: str, round_no: int, reviewer: str, findings: int, ago: float) -> list[tuple]:
    found = [{"path": "a.py", "line": n, "body": f"Finding {n}."} for n in range(1, findings + 1)]
    verdict = "changes" if findings else "clean"
    return [
        (task, "review_submitted", {"verdict": verdict, "summary": "S.", "findings": found}, ago),
        (task, "pull_request_reviewed", {"round": round_no, "verdict": verdict, "reviewer": reviewer}, ago),
        (task, "attempt_finished", {"tier": "review", "candidate": reviewer, "spent_usd": "0.40"}, ago),
    ]


def test_each_reviewer_has_its_rounds_findings_outcomes_and_cost_within_the_range():
    recorded = _events(
        # Opus raised three findings on t-1: the next round (by Sol) judged two, and an operator dismissed one.
        *_review("t-1", 1, OPUS, 3, 2),
        ("t-1", "finding_dismissed", {"round": 1, "finding": 3, "reason": "Not ours."}, 2),
        *_review("t-1", 2, SOL, 0, 1),
        ("t-1", "finding_resolved", {"round": 1, "finding": 1, "outcome": "fixed", "judged_in": 2}, 1),
        ("t-1", "finding_resolved", {"round": 1, "finding": 2, "outcome": "not_fixed", "judged_in": 2}, 1),
        # Opus on t-2, long ago: outside a week.
        *_review("t-2", 1, OPUS, 2, 40),
        # A fix run's cost is not a review's.
        ("t-1", "attempt_finished", {"tier": "fix", "candidate": OPUS, "spent_usd": "3.00"}, 1),
    )

    rows = reviewers.table(recorded, NOW - timedelta(days=7), NOW + timedelta(seconds=1))

    assert rows == [
        {
            "reviewer": OPUS,
            "rounds": 1,
            "findings": 3,
            "fixed": 1,
            "not_fixed": 1,
            "dismissed": 1,
            "never_judged": 0,
            "cost_usd": "0.40",
        },
        {
            "reviewer": SOL,
            "rounds": 1,
            "findings": 0,
            "fixed": 0,
            "not_fixed": 0,
            "dismissed": 0,
            "never_judged": 0,
            "cost_usd": "0.40",
        },
    ]


def test_a_last_rounds_findings_are_never_judged_and_events_are_read_page_by_page():
    recorded = _events(*_review("t-3", 1, OPUS, 2, 1))
    [row] = reviewers.table(recorded, NOW - timedelta(days=7), NOW + timedelta(seconds=1))
    assert (row["findings"], row["never_judged"]) == (2, 2)

    pages = []

    def read(after, limit, **filters):
        pages.append(after)
        start = [e["position"] for e in recorded].index(after) + 1 if after else 0
        return {"events": recorded[start : start + 2]}

    reviewers.PAGE, old = 2, reviewers.PAGE
    try:
        assert reviewers.read_all(read, reviewers.EVENT_TYPES) == recorded
    finally:
        reviewers.PAGE = old
    assert pages == [0, 2], "the second page starts after the first page's last event"
