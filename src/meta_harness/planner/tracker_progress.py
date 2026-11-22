"""Telling an issue's tracker how its task is going: its status, a comment, and the pull request's link.

Only for a tracker whose adapter asks for it (`writes_progress`). Jira does: nothing there shows a task's
progress otherwise, and an issue is done only when something moves it to Done, which is also what lets a goal
be met. GitHub does not: a pull request's `Closes` line links it to its issue and closes the issue on merge.

A sweep works out, for each of the goal's issues that has a task, what the tracker should now say, and writes
only when that differs from what it last wrote (`issue_link.progress`), so a sweep that finds nothing new
writes nothing, and a write that failed is tried again by the next sweep. The status goes first and the
comment last: the status and the link are safe to repeat, and the comment is what a repeat would double.
"""

import asyncio
import logging
from dataclasses import dataclass

from meta_harness import outbound
from meta_harness import db
from meta_harness.planner import assignees, store
from meta_harness.run_service.provenance import COMMENT, Provenance
from meta_harness.run_service.shared import ALREADY_DONE, ALREADY_DONE_REASON
from meta_harness.trackers.base import DONE, IN_PROGRESS, IN_REVIEW, Tracker, TrackerError

log = logging.getLogger("meta_harness.planner.tracker_progress")

# Every status a task may have: the sweep reads all the goal's tasks at once.
TASK_STATUSES = [
    "queued",
    "blocked",
    "running",
    "taken_over",
    "ready",
    "review_unresolved",
    "closed",
    "failed",
    "canceled",
]
TASKS_READ = 1000


@dataclass(frozen=True)
class Told:
    """What a tracker is told about an issue: the key kept to know it was told, the status to set (None: leave
    the issue where it is), and the comment."""

    key: str
    status: str | None
    comment: str
    # The comment holds a model's words (what a check of the base branch found), so it gets the writing pass.
    generated: bool = False
    # Told to every tracker, not only one that is told a task's progress: nothing else would close the issue.
    every_tracker: bool = False


def told_for(task: dict, before: str | None = None) -> Told | None:
    """What the task's state says to its issue's tracker, given what the tracker was last told (before); None
    when there is nothing new to say: a task that has not started, or one at work that the tracker already
    shows right."""
    status, pr_url = task["status"], task.get("pr_url") or ""
    why = (task.get("status_reason") or "").strip()
    if status in ("running", "taken_over"):
        started = Told("in_progress", IN_PROGRESS, f"The meta-harness started work on this issue (task {task['id']}).")
        if not before or before in ("failed", "canceled"):
            return started
        # At work with a pull request, the issue goes back to in progress only from a review a person was
        # waiting on, and only when the work is more than the merge they asked for: the agents' own review of
        # a new pull request, and a merge under way, leave the issue where it is.
        if pr_url and before.startswith("in_review:") and task.get("resume") != "merge":
            return Told(f"in_progress:{pr_url}", IN_PROGRESS, f"Work is going on again on the pull request: {pr_url}")
        return None
    if status in ("ready", "review_unresolved") and pr_url:
        waits = "it is ready to merge" if status == "ready" else "its review left findings for a person to settle"
        return Told(f"in_review:{status}:{pr_url}", IN_REVIEW, f"A pull request is open, and {waits}: {pr_url}")
    if status == "closed" and why.startswith(ALREADY_DONE_REASON):
        # The run changed nothing because the base branch has the work: no pull request will close the issue.
        found = why.removeprefix(ALREADY_DONE_REASON).strip().rstrip(".")
        said = f"Already done: {found}. No change was needed, so the task opened no pull request."
        return Told(ALREADY_DONE, DONE, said, generated=True, every_tracker=True)
    if status == "closed":
        return Told("done", DONE, f"Done: the pull request merged. {pr_url}".strip())
    if status == "failed":
        return Told("failed", None, f"The task failed: {why or 'no reason was recorded'}. An operator can retry it.")
    if status == "canceled":
        return Told("canceled", None, f"The task was canceled: {why or 'no reason was recorded'}.")
    return None


async def write(goal: dict, tracker: Tracker, tasks, errors: list[str], events=None) -> list[str]:
    """Tell the goal's tracker what changed for each of its issues since the last sweep. Returns the refs
    written to; a write that fails is added to errors and is tried again next sweep. With events, an issue
    whose task has started and that has no assignee is assigned first, on every tracker (assignees.py)."""
    progress = bool(getattr(tracker, "writes_progress", False))
    links = [link for link in await asyncio.to_thread(_links, goal["id"]) if link["task_id"]]
    # An issue still open whose task may have closed as already done: told on any tracker.
    unsettled = [link for link in links if link.get("state") == "open" and link.get("progress") != ALREADY_DONE]
    if not links or not (progress or unsettled or assignees.pending(links)):
        return []
    by_id = {task["id"]: task for task in await asyncio.to_thread(tasks.list, TASK_STATUSES, goal["id"], TASKS_READ)}
    if events is not None:
        await assignees.assign_started(goal, tracker, links, by_id, events)
    written = []
    for link in links:
        task = by_id.get(link["task_id"])
        told = told_for(task, link.get("progress")) if task else None
        if told is None or told.key == link.get("progress") or not (progress or told.every_tracker):
            continue
        try:
            if told.status:
                await tracker.set_status(link["ref"], told.status)
            if task.get("pr_url"):
                await tracker.link_pull_request(link["ref"], task["pr_url"], task.get("title") or task["pr_url"])
            signed = Provenance(COMMENT, goal=goal["id"], task=task["id"])
            await tracker.comment(
                link["ref"],
                await outbound.for_tracker(
                    told.comment,
                    link["ref"],
                    outbound.COMMENT,
                    outbound.GENERATED if told.generated else outbound.FIXED,
                    provenance=signed,
                ),
            )
        except TrackerError as err:
            errors.append(f"{link['ref']}: could not tell its tracker {told.key.split(':')[0]}: {err}")
            continue
        await asyncio.to_thread(_save, link["ref"], told.key)
        written.append(link["ref"])
    return written


def _links(goal_id: str) -> list[dict]:
    with db.connect(autocommit=True) as conn:
        return store.links(conn, goal_id)


def _save(ref: str, key: str) -> None:
    with db.connect(autocommit=True) as conn:
        store.set_link_progress(conn, ref, key)
