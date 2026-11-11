"""Every pull request the meta-harness opened lately, by state, for the Overview's Pull requests row.

A pull request is the one a task opened, with its review and fix runs. Its
facts come from their owners: the task's status, pull request URL and times
from the Task API, and its fix rounds and runs from the events recorded on
it. No query reads another component's tables. What the work cost is the
Performance page's, from GET /v1/costs, not this row's.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from meta_harness.planner.progress import EventReader

# The row shows at most this many pull requests, the newest, and says how many it leaves out.
ROW_LIMIT = 500
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
    """What a task's pull request is waiting on now: in_review, waiting, blocked, merged or closed.

    Merged or closed is what the pull request's end events say, not the task's status alone: an operator can
    cancel a task and leave its pull request open, to merge or close by hand, and the sweep records that end
    when it comes (sweep.py). Till then the pull request is open, with no workflow on it: waiting on an
    operator, as a ready one is."""
    kinds = {e["event_type"] for e in events}
    if "pull_request_merged" in kinds or task["status"] == "closed":
        return "merged"
    if "pull_request_closed" in kinds:
        return "closed"
    if task["status"] in ("ready", "canceled"):
        return "waiting"
    if task["status"] in ("review_unresolved", "failed"):
        return "blocked"
    return "in_review"


def activity(tasks: list[dict], reader: EventReader, days: int, now: datetime | None = None) -> dict[str, Any]:
    """The pull requests of the tasks started in the last days, oldest first, each with its state and latest
    run, so the Overview shows what waits on an operator and opens a pull request's latest run."""
    since = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    recent = [t for t in tasks if t.get("pr_url") and datetime.fromisoformat(t["created_at"]) >= since]
    events = _events(reader, [t["id"] for t in recent]) if recent else {}
    prs = []
    for task in recent:
        mine = events.get(task["id"], [])
        attempts = [e for e in mine if e["event_type"] == "attempt_finished"]
        opened = next((e for e in mine if e["event_type"] == "pull_request_opened"), None)
        ended = next(
            (e for e in reversed(mine) if e["event_type"] in ("pull_request_merged", "pull_request_closed")), None
        )
        prs.append(
            {
                "task_id": task["id"],
                "latest_run_id": next((e.get("run_id") for e in reversed(attempts) if e.get("run_id")), None),
                "title": task["title"],
                "repo": task["repo"],
                "pr_url": task["pr_url"],
                "opened_at": (opened or {}).get("time_utc") or task["created_at"],
                "state": pr_state(task, mine),
                "state_at": (ended or {}).get("time_utc") or task["updated_at"],
                "fix_rounds": sum(1 for e in attempts if e["payload"].get("tier") == "fix"),
            }
        )
    prs.sort(key=lambda p: p["opened_at"])
    return {"days": days, "total": len(prs), "left_out": max(0, len(prs) - ROW_LIMIT), "prs": prs[-ROW_LIMIT:]}
