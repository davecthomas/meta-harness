"""What the work costs: the numbers behind GET /v1/performance and the console's Performance page.

Over a time range it takes every task whose last run finished in the range, and from the events works out, for
each, what it cost (every run: task, review and fix), how long its runs took, and how far it succeeded. From
those: the cost per task, per pull request and per goal, each as an average and a median, and the key metric:

    dollar-hours per successfully completed task = Σi (Di × Hi) / Σi Ci

- Di, dollars for attempt i: its `attempt_finished` event's `spent_usd`, what the HTTP Gateway counted.
- Hi, hours for attempt i: from the run's `run_prompt` event, recorded as it starts, to its `attempt_finished`.
- Ci, success: the share of the task's checks that passed. Two are recorded, so two are counted: the code
  host's checks at the pull request's last checked head, and the merge. A task's acceptance checks run inside
  the review and are not recorded apart, so they are not a share of their own yet.

A deploy's smoke task is no work and is left out of everything. A goal's planning task is counted in its
goal's cost and in the total, and is not one of the tasks: it opens no pull request and has nothing to merge.

The average is that ratio over the range's tasks. The median is the middle task's own Σ(D × H), among the tasks
that merged. Spend on tasks that did not merge is the wasted spend. Nothing is stored: it is read from the
events each time (tasks/reviewers.py reads them the same way).
"""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any

EVENT_TYPES = ("attempt_finished", "run_prompt", "pull_request_opened", "pull_request_checks", "pull_request_merged")
CENT = Decimal("0.01")


def _at(time_utc: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(time_utc).replace("Z", "+00:00"))
    except ValueError:
        return None


def _usd(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except InvalidOperation:
        return Decimal(0)


def per_task(events: list[dict]) -> dict[str, dict]:
    """Each task's cost, hours, dollar-hours and success, from its events, with when its last run finished."""
    started: dict[str, datetime] = {}
    found: dict[str, dict] = defaultdict(
        lambda: {
            "usd": Decimal(0),
            "review_usd": Decimal(0),
            "hours": 0.0,
            "dollar_hours": 0.0,
            "runs": 0,
            "pr": False,
            "checks": None,
            "merged": False,
            "last": None,
        }
    )
    for event in sorted(events, key=lambda e: e["position"]):
        task_id, payload, kind = event.get("task_id"), event.get("payload") or {}, event["event_type"]
        if not task_id:
            continue
        at = _at(event.get("time_utc"))
        task = found[task_id]
        if kind == "run_prompt":
            run = payload.get("run_id") or event.get("run_id")
            if run and at and run not in started:
                started[run] = at
        elif kind == "attempt_finished":
            usd = _usd(payload.get("spent_usd"))
            begun = started.get(event.get("run_id") or "")
            hours = max(0.0, (at - begun).total_seconds() / 3600) if at and begun else 0.0
            task["usd"] += usd
            if payload.get("tier") in ("review", "fix"):
                task["review_usd"] += usd
            task["hours"] += hours
            task["dollar_hours"] += float(usd) * hours
            task["runs"] += 1
            if at and (task["last"] is None or at > task["last"]):
                task["last"] = at
        elif kind == "pull_request_opened":
            task["pr"] = True
        elif kind == "pull_request_checks" and payload.get("state") in ("passed", "failed"):
            # The newest stands: the checks at the last head the task had checked.
            task["checks"] = payload["state"] == "passed"
        elif kind == "pull_request_merged":
            task["merged"] = True
            task["pr"] = True
    for task in found.values():
        # The share of its recorded checks that passed: the code host's checks, and the merge.
        task["success"] = (int(bool(task["checks"])) + int(task["merged"])) / 2
    return dict(found)


def _both(values: list[float]) -> dict[str, Any]:
    """The average and the median of some amounts, and how many there are. None with nothing to count."""
    if not values:
        return {"average": None, "median": None, "count": 0}
    return {"average": round(sum(values) / len(values), 4), "median": round(median(values), 4), "count": len(values)}


# Task types that are not work on a repo: a deploy's smoke task is left out, and a goal's planning task is only
# part of its goal's cost.
SMOKE, PLAN = "smoke", "plan"


def summary(
    events: list[dict],
    goals: dict[str, str | None],
    since: datetime,
    until: datetime,
    types: dict[str, str] | None = None,
) -> dict:
    """The Performance page's numbers for the tasks whose last run finished in [since, until). goals: each
    task's goal, by task id (None for a task of no goal). types: each task's type, by task id."""
    types = types or {}
    ran = {
        task_id: task
        for task_id, task in per_task(events).items()
        if task["last"] is not None and since <= task["last"] < until and task["runs"] and types.get(task_id) != SMOKE
    }
    planning = sum((t["usd"] for task_id, t in ran.items() if types.get(task_id) == PLAN), Decimal(0))
    tasks = {task_id: task for task_id, task in ran.items() if types.get(task_id) != PLAN}
    costs = [float(t["usd"]) for t in tasks.values()]
    with_pr = [float(t["usd"]) for t in tasks.values() if t["pr"]]
    by_goal: dict[str, float] = defaultdict(float)
    for task_id, task in ran.items():
        if goals.get(task_id):
            by_goal[goals[task_id]] += float(task["usd"])
    merged = [t for t in tasks.values() if t["merged"]]
    succeeded = sum(t["success"] for t in tasks.values())
    total = sum((t["usd"] for t in ran.values()), Decimal(0))
    wasted = sum((t["usd"] for t in tasks.values() if not t["merged"]), Decimal(0))
    review = sum((t["review_usd"] for t in tasks.values()), Decimal(0))
    return {
        "tasks": len(tasks),
        "merged": len(merged),
        "cost_per_task": _both(costs),
        "cost_per_pull_request": _both(with_pr),
        "cost_per_goal": _both(list(by_goal.values())),
        "dollar_hours_per_completed_task": {
            # Σ(D × H) over every attempt of the range's tasks, over Σ C: failed work counts against the number.
            "average": round(sum(t["dollar_hours"] for t in tasks.values()) / succeeded, 4) if succeeded else None,
            "median": round(median(t["dollar_hours"] for t in merged), 4) if merged else None,
            "count": len(merged),
        },
        "total_usd": str(total.quantize(CENT)),
        "wasted_usd": str(wasted.quantize(CENT)),
        "review_loop_usd": str(review.quantize(CENT)),
        "planning_usd": str(planning.quantize(CENT)),
        "hours": round(sum(t["hours"] for t in tasks.values()), 2),
    }
