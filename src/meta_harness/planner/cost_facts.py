"""What the cost service asks of the planner about each task: its issue's labels and its pull request's outcome.

Costs are recorded by task (costs/model_source.py), but a label or an outcome is not a cost's: an issue is
relabeled and a pull request merges long after its runs cost what they cost. So they are looked up when costs
are asked for, never stored on a cost row: the labels from the planner's own issue links, and the outcome from
the Task API's task, whose status the planner sets when it sees the pull request merge or close (sweep.py).
"""

from typing import Any

import psycopg

from meta_harness.planner import store


def outcome(task: dict) -> str:
    """merged, closed (without merging), open (not yet either), or none (no pull request was opened): one of
    the cost API's OUTCOMES (costs/api.py)."""
    if not task.get("pr_url"):
        return "none"
    if task.get("status") == "closed":
        return "merged"
    if task.get("status") == "canceled":
        return "closed"
    return "open"


def task_facts(conn: psycopg.Connection, tasks: list[dict]) -> dict[str, dict[str, Any]]:
    """For each task, its issue's labels and its outcome. A task made from no issue has no labels; one the
    Task API did not list has no outcome, and so matches no outcome filter."""
    facts: dict[str, dict[str, Any]] = {
        task["id"]: {"labels": [], "outcome": outcome(task)} for task in tasks if task.get("id")
    }
    for task_id, labels in store.task_labels(conn).items():
        facts.setdefault(task_id, {"labels": [], "outcome": None})["labels"] = labels
    return facts
