"""Each of a goal's issues with its task's progress: status, pull request, its state and its checks.

A task's facts come from their owners, not from another component's tables:
its status and pull request URL from the Task API, and what became of the
pull request from the events the RunTask workflow and the planner's sweep
recorded on the task (`pull_request_opened`, `pull_request_checks`,
`pull_request_merged`, `pull_request_closed`).
"""

from typing import Any, Protocol

PR_EVENT_TYPES = ("pull_request_opened", "pull_request_checks", "pull_request_merged", "pull_request_closed")
# Task ids per event read, so a goal with many tasks never makes one long URL.
TASKS_PER_READ = 100
EVENTS_PER_READ = 5000


class EventReader(Protocol):
    def read(
        self, from_position: int = 0, limit: int = 500, before_position: int | None = None, **filters: str
    ) -> dict[str, Any]: ...


def pull_request_events(reader: EventReader, task_ids: list[str]) -> list[dict]:
    """Every pull request event recorded on these tasks, in position order."""
    found: list[dict] = []
    for start in range(0, len(task_ids), TASKS_PER_READ):
        chunk = ",".join(task_ids[start : start + TASKS_PER_READ])
        position = 0
        while True:
            page = reader.read(
                from_position=position, limit=EVENTS_PER_READ, task_id=chunk, event_type=",".join(PR_EVENT_TYPES)
            )
            found += page["events"]
            if len(page["events"]) < EVENTS_PER_READ:
                break
            position = page["next_position"]
    return sorted(found, key=lambda event: event["position"])


def pull_request_view(task: dict | None, events: list[dict]) -> dict[str, Any]:
    """A task's pull request URL, its state (open, merged or closed) and its newest recorded checks.

    checks is the newest head commit's recorded checks, passed or failed with the failed names, or
    pending while an open pull request has none recorded yet. events are the task's, in position order.
    """
    pr_url = (task or {}).get("pr_url")
    state, checks = None, None
    for event in events:
        payload = event.get("payload") or {}
        kind = event["event_type"]
        if kind == "pull_request_opened":
            pr_url = pr_url or payload.get("pr_url")
            state = state or "open"
        elif kind == "pull_request_checks":
            checks = {
                "head_sha": payload.get("head_sha"),
                "state": payload.get("state"),
                "failed": list(payload.get("failed") or []),
            }
        elif kind == "pull_request_merged":
            state = "merged"
        elif kind == "pull_request_closed" and state != "merged":
            state = "closed"
        pr_url = pr_url or payload.get("pr_url")
    if pr_url and state is None:
        state = "open"
    if state == "open" and checks is None:
        checks = {"head_sha": (task or {}).get("head_sha"), "state": "pending", "failed": []}
    return {"pr_url": pr_url, "pr_state": state, "checks": checks}


def issues_progress(links: list[dict], tasks: list[dict], events: list[dict]) -> list[dict]:
    """Each issue link with its task's status, pull request URL, pull request state and checks."""
    by_id = {task["id"]: task for task in tasks}
    by_task: dict[str, list[dict]] = {}
    for event in events:
        by_task.setdefault(event.get("task_id") or "", []).append(event)
    shown = []
    for link in links:
        task_id = link.get("task_id")
        task = by_id.get(task_id) if task_id else None
        view = pull_request_view(task, by_task.get(task_id, [])) if task_id else _no_pull_request()
        shown.append(
            {
                "ref": link["ref"],
                # The issue's title, for a card with no task yet. None until a sweep has read the issue.
                "title": link.get("title"),
                "repo": link["repo"],
                "state": link["state"],
                "depends_on": link.get("depends_on") or [],
                "refused_reason": link.get("refused_reason"),
                "task_id": task_id,
                "task_status": task["status"] if task else None,
                "task_status_reason": task.get("status_reason") if task else None,
                # "budget" while the running task is held for budget, waiting on an operator.
                "task_waiting_on": task.get("waiting_on") if task else None,
                **view,
            }
        )
    return shown


def _no_pull_request() -> dict[str, Any]:
    return {"pr_url": None, "pr_state": None, "checks": None}
