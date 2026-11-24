"""How each reviewer's findings turned out: the table behind GET /v1/reviewers and the console's Reviewers page.

A reviewer is a pair from the task policy's review.reviewers, `harness:<profile>/<engine>:<model>`, as each
round's `pull_request_reviewed` names it. Over a time range, it counts each reviewer's rounds and findings, and
what became of the findings (tasks/reviews.py): fixed or not fixed as the next round's reviewer judged them,
dismissed by an operator, or never judged, which is every finding of a round no later round followed. It adds
what the reviewer's review runs cost (`attempt_finished` with tier review). Nothing is stored: it is read from
the events each time.
"""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from meta_harness.tasks import reviews

# What the table reads: each task's review events, and its runs' costs.
EVENT_TYPES = (*reviews.REVIEW_EVENT_TYPES,)
# How many events one read of the event service returns.
PAGE = 2000
OUTCOMES = ("fixed", "not_fixed", "dismissed")


def read_all(read: Callable[..., dict], event_types: tuple[str, ...]) -> list[dict]:
    """Every event of these types, oldest first, read page by page by position."""
    found: list[dict] = []
    after = 0
    while True:
        page = read(after, PAGE, event_type=",".join(event_types))["events"]
        found += page
        if len(page) < PAGE:
            return found
        after = page[-1]["position"]


def _within(time_utc: Any, since: datetime, until: datetime) -> bool:
    try:
        at = datetime.fromisoformat(str(time_utc).replace("Z", "+00:00"))
    except ValueError:
        return False
    return since <= at < until


def _usd(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except InvalidOperation:
        return Decimal(0)


def table(events: list[dict], since: datetime, until: datetime) -> list[dict]:
    """One row per reviewer with a round in [since, until): rounds, findings, each outcome's count, the findings
    never judged, and the cost of its review runs in that range. Most rounds first."""
    by_task: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        if event.get("task_id"):
            by_task[event["task_id"]].append(event)
    rows: dict[str, dict] = {}

    def row(reviewer: str) -> dict:
        return rows.setdefault(
            reviewer,
            {
                "reviewer": reviewer,
                "rounds": 0,
                "findings": 0,
                **{o: 0 for o in OUTCOMES},
                "never_judged": 0,
                "cost_usd": Decimal(0),
            },
        )

    for task_id, recorded in by_task.items():
        # Only the rounds are counted, not what came next, so the task itself is not read.
        for shown in reviews.rounds({"id": task_id, "status": "", "status_reason": None}, recorded):
            if not shown.get("reviewer") or not _within(shown.get("reviewed_at"), since, until):
                continue
            counted = row(shown["reviewer"])
            counted["rounds"] += 1
            for finding in shown["findings"]:
                counted["findings"] += 1
                counted[finding["outcome"] if finding.get("outcome") in OUTCOMES else "never_judged"] += 1
        for event in recorded:
            payload = event.get("payload") or {}
            if (
                event["event_type"] == "attempt_finished"
                and payload.get("tier") == "review"
                and payload.get("candidate")
                and _within(event.get("time_utc"), since, until)
            ):
                row(payload["candidate"])["cost_usd"] += _usd(payload.get("spent_usd"))
    ordered = sorted(rows.values(), key=lambda r: (-r["rounds"], r["reviewer"]))
    return [{**r, "cost_usd": str(r["cost_usd"].quantize(Decimal("0.01")))} for r in ordered]
