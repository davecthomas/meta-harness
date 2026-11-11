"""What the cost service asks of the planner about each task: its issue's labels and its pull request's outcome.

Costs are recorded by task (costs/model_source.py), but a label or an outcome is not a cost's: an issue is
relabeled and a pull request merges long after its runs cost what they cost. So they are looked up when costs
are asked for, never stored on a cost row: the labels from the planner's own issue links, which each sweep
refreshes (sweep.py), and the outcome from the pull request's own events, `pull_request_merged` and
`pull_request_closed`, which the run workflow or the planner's sweep records when either sees the pull request
end. The task's status is not the outcome: an operator can cancel a task and leave its pull request open.
"""

from typing import Any, Protocol

import psycopg

from meta_harness.planner import store

# A pull request's end, as its events record it.
END_EVENTS = {"pull_request_merged": "merged", "pull_request_closed": "closed"}
EVENTS_PER_READ = 5000
# The most tasks one Task API page lists.
TASKS_PER_PAGE = 1000


class EventReader(Protocol):
    def read(
        self, from_position: int = 0, limit: int = 500, before_position: int | None = None, **filters: str
    ) -> dict[str, Any]: ...


class TaskPages(Protocol):
    def page(
        self, statuses: list[str] | None = None, goal_id: str | None = None, limit: int = 25, offset: int = 0
    ) -> dict: ...


def every_task(tasks: TaskPages, goal_id: str | None = None) -> list[dict]:
    """Every task the Task API holds, page after page: one page is only the first thousand, by priority then
    age, and leaves out the newest once there are more."""
    found: dict[str, dict] = {}
    offset = 0
    while True:
        page = tasks.page(None, goal_id, TASKS_PER_PAGE, offset)
        batch = page.get("tasks") or []
        for task in batch:
            found.setdefault(task["id"], task)
        offset += len(batch)
        if not batch or offset >= int(page.get("total") or 0):
            return list(found.values())


def pull_request_ends(reader: EventReader) -> dict[str, str]:
    """For each task whose pull request ended, how: merged or closed (without merging), by its latest end."""
    ends: dict[str, str] = {}
    position = 0
    while True:
        page = reader.read(from_position=position, limit=EVENTS_PER_READ, event_type=",".join(END_EVENTS))
        events = page.get("events") or []
        for event in sorted(events, key=lambda e: e["position"]):
            if event.get("task_id"):
                ends[event["task_id"]] = END_EVENTS[event["event_type"]]
        if len(events) < EVENTS_PER_READ:
            return ends
        position = max(e["position"] for e in events)


def outcome(task: dict, ended: str | None = None) -> str:
    """merged, closed (without merging), open (not yet either), or none (no pull request was opened): one of
    the cost API's OUTCOMES (costs/api.py). ended is how the pull request's events say it ended, if they do.
    Without one, a closed task's pull request merged (the Task API closes a task only on its merge), and any
    other task's is still open, a canceled one's included."""
    if not task.get("pr_url"):
        return "none"
    if ended:
        return ended
    return "merged" if task.get("status") == "closed" else "open"


def task_facts(conn: psycopg.Connection, tasks: list[dict], ends: dict[str, str]) -> dict[str, dict[str, Any]]:
    """For each task, its issue's labels and its outcome, given how each ended pull request ended. A task made
    from no issue has no labels; one the Task API did not list has no outcome, and so matches no outcome
    filter."""
    facts: dict[str, dict[str, Any]] = {
        task["id"]: {"labels": [], "outcome": outcome(task, ends.get(task["id"]))} for task in tasks if task.get("id")
    }
    for task_id, labels in store.task_labels(conn).items():
        facts.setdefault(task_id, {"labels": [], "outcome": None})["labels"] = labels
    return facts
