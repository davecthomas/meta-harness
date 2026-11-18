"""Where a task's money went: one row per run, from the events its runs recorded.

A run's cost and how it ended come from its attempt_finished event (the run service's own count, from the
run's grant). Its turns and tokens come from its run_finished event (the harness server's count), when the
run got far enough to report one. Nothing here reads the run service's tables.

The HTTP Gateway's `input_tokens` leaves out what a turn wrote to the provider's prompt cache, so a run's
input here is that count plus its cache writes: everything the model read. Without them a run that caches
its whole prompt reads as 100% from the cache.

The writing pass's rewrites of a task's posts (`writing_checked`) cost money too, outside any run and so
outside the task's spend limit. The totals name that cost beside the runs', and `all_usd` adds the two: the
figure the cost service gives for the task.
"""

from decimal import Decimal
from typing import Any

SPEND_EVENT_TYPES = ("attempt_finished", "run_finished", "writing_checked")
# The kinds a run is counted under. A run that is neither a review nor a fix is one of the task's own runs.
KINDS = ("task", "review", "fix")


def kind_of(tier: str | None) -> str:
    return tier if tier in ("review", "fix") else "task"


def read_tokens(usage: dict[str, Any]) -> int | None:
    """Every input token a run's model read: the gateway's count and what it wrote to the prompt cache."""
    if usage.get("input_tokens") is None:
        return None
    written = int(usage.get("cache_write_5m_tokens") or 0) + int(usage.get("cache_write_1h_tokens") or 0)
    return int(usage["input_tokens"]) + written


def writing(events: list[dict[str, Any]]) -> Decimal:
    """What the writing pass's rewrites of the task's posts cost."""
    checked = (e["payload"] or {} for e in events if e["event_type"] == "writing_checked")
    return sum((Decimal(str(payload.get("usd_cost") or 0)) for payload in checked), Decimal(0))


def runs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The task's runs, oldest first: kind, candidate, turns, tokens, cost, and how each ended."""
    finished = {e.get("run_id"): e["payload"] or {} for e in events if e["event_type"] == "run_finished"}
    rows = []
    for event in sorted((e for e in events if e["event_type"] == "attempt_finished"), key=lambda e: e["position"]):
        attempt = event["payload"] or {}
        report = finished.get(event.get("run_id"), {})
        usage = report.get("usage") or {}
        rows.append(
            {
                "run_id": event.get("run_id"),
                "attempt": attempt.get("attempt"),
                "kind": kind_of(attempt.get("tier")),
                "tier": attempt.get("tier"),
                "candidate": attempt.get("candidate"),
                "turns": report.get("turns"),
                "model_calls": usage.get("calls"),
                "input_tokens": read_tokens(usage),
                "cached_input_tokens": usage.get("cached_input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "usd": str(Decimal(str(attempt.get("spent_usd") or 0))),
                "status": attempt.get("status"),
                "end_reason": attempt.get("end_reason"),
            }
        )
    return rows


def totals(rows: list[dict[str, Any]], writing_usd: Decimal = Decimal(0)) -> dict[str, Any]:
    """What the runs spent in all and by kind, with their turns and tokens added up. `usd` is the runs' cost,
    which the spend limit counts; `all_usd` adds writing_usd, the writing pass's."""
    by_kind = {kind: Decimal(0) for kind in KINDS}
    for row in rows:
        by_kind[row["kind"]] += Decimal(row["usd"])

    def added(key: str) -> int:
        return sum(int(row[key] or 0) for row in rows)

    spent = sum(by_kind.values(), Decimal(0))
    return {
        "usd": str(spent),
        "writing_usd": str(writing_usd),
        "all_usd": str(spent + writing_usd),
        "by_kind": {kind: str(amount) for kind, amount in by_kind.items()},
        "runs": len(rows),
        "turns": added("turns"),
        "input_tokens": added("input_tokens"),
        "cached_input_tokens": added("cached_input_tokens"),
        "output_tokens": added("output_tokens"),
    }
