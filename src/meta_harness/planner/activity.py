"""Every pull request the meta-harness opened, grouped by goal, repo or label, for the console's heat map.

A pull request is the one a task opened, with its review and fix runs. Its
facts come from their owners: the task's status, pull request URL and times
from the Task API, the goal's name and label from the planner's own goals,
and what the task spent, its fix rounds and its runs from the events
recorded on it. No query reads another component's tables.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from meta_harness.planner.progress import EventReader

# A row shows at most this many pull requests, the newest, and says how many it leaves out.
ROW_LIMIT = 200
EVENT_TYPES = ("attempt_finished", "pull_request_opened", "pull_request_merged", "pull_request_closed")
TASKS_PER_READ = 100
EVENTS_PER_READ = 5000


def _events(reader: EventReader, task_ids: list[str]) -> dict[str, list[dict]]:
    """Each task's attempts and pull request events, in position order."""
    by_task: dict[str, list[dict]] = {}
    for start in range(0, len(task_ids), TASKS_PER_READ):
        chunk = ",".join(task_ids[start : start + TASKS_PER_READ])
        position = 0
        while True:
            page = reader.read(
                from_position=position, limit=EVENTS_PER_READ, task_id=chunk, event_type=",".join(EVENT_TYPES)
            )
            for event in page["events"]:
                by_task.setdefault(event.get("task_id") or "", []).append(event)
            if len(page["events"]) < EVENTS_PER_READ:
                break
            position = page["next_position"]
    for events in by_task.values():
        events.sort(key=lambda e: e["position"])
    return by_task


def pr_state(task: dict, events: list[dict]) -> str:
    """What a task's pull request is waiting on now: in_review, waiting, blocked, merged or closed."""
    kinds = {e["event_type"] for e in events}
    if "pull_request_merged" in kinds or task["status"] == "closed":
        return "merged"
    if "pull_request_closed" in kinds or task["status"] == "canceled":
        return "closed"
    if task["status"] == "ready":
        return "waiting"
    if task["status"] in ("review_unresolved", "failed"):
        return "blocked"
    return "in_review"


def _percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * (len(ordered) - 1) + 0.5))] if ordered else 0.0


def activity(
    tasks: list[dict],
    goals: dict[str, dict],
    reader: EventReader,
    group_by: str,
    days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The pull requests of the tasks started in the last days, grouped, with the cost scale for the map."""
    since = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    recent = [t for t in tasks if datetime.fromisoformat(t["created_at"]) >= since]
    events = _events(reader, [t["id"] for t in recent]) if recent else {}
    groups: dict[str, dict[str, Any]] = {}
    costs: list[float] = []
    for task in recent:
        mine = events.get(task["id"], [])
        attempts = [e for e in mine if e["event_type"] == "attempt_finished"]
        usd = float(sum((Decimal(str(e["payload"].get("spent_usd") or 0)) for e in attempts), Decimal(0)))
        latest_run = next((e.get("run_id") for e in reversed(attempts) if e.get("run_id")), None)
        goal = goals.get(task.get("goal_id") or "")
        if group_by == "repo":
            key, name = task["repo"], task["repo"]
        elif group_by == "label":
            key = (goal or {}).get("label") or "(picked issues)"
            name = key
        else:
            key = task.get("goal_id") or "(no goal)"
            name = (goal.get("title") or goal["statement"][:80]) if goal else "Tasks with no goal"
        group = groups.setdefault(key, {"key": key, "name": name, "prs": [], "spent_without_pr": []})
        if task.get("pr_url"):
            opened = next((e for e in mine if e["event_type"] == "pull_request_opened"), None)
            ended = next(
                (e for e in reversed(mine) if e["event_type"] in ("pull_request_merged", "pull_request_closed")), None
            )
            group["prs"].append(
                {
                    "task_id": task["id"],
                    "latest_run_id": latest_run,
                    "title": task["title"],
                    "repo": task["repo"],
                    "pr_url": task["pr_url"],
                    "opened_at": (opened or {}).get("time_utc") or task["created_at"],
                    "state": pr_state(task, mine),
                    "state_at": (ended or {}).get("time_utc") or task["updated_at"],
                    "fix_rounds": sum(1 for e in attempts if e["payload"].get("tier") == "fix"),
                    "usd": round(usd, 4),
                }
            )
            costs.append(usd)
        elif usd > 0:
            group["spent_without_pr"].append(
                {
                    "task_id": task["id"],
                    "latest_run_id": latest_run,
                    "title": task["title"],
                    "started_at": task["created_at"],
                    "usd": round(usd, 4),
                }
            )
    shown = []
    for group in groups.values():
        prs = sorted(group["prs"], key=lambda p: p["opened_at"])
        group["total"] = len(prs)
        group["left_out"] = max(0, len(prs) - ROW_LIMIT)
        group["prs"] = prs[-ROW_LIMIT:]
        group["spent_without_pr"].sort(key=lambda p: p["started_at"])
        shown.append(group)
    shown.sort(key=lambda g: (-g["total"], g["name"]))
    return {
        "group_by": group_by,
        "days": days,
        "groups": shown,
        "cost_scale": {"low": round(_percentile(costs, 0.05), 4), "high": round(_percentile(costs, 0.95), 4)},
    }
