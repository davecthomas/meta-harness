"""A split issue's own failed task ends when the parts did its work.

An issue split into parts is replaced by them: the parts run, and the issue closes once they are done. An
issue that was also run as one (Run anyway) kept a task of its own beside its parts. When that task failed
and every part merged, nothing ended it: the goal's page asked the operator to retry it, which would have
written the parts' merged work a second time, while the planner's update said the goal was done.

The work is done, so the sweep ends the task itself (docs/technical-design.md, Goal seeking: close it when
merged work already covers it). The task closes with the reason, and the issue goes back to being replaced
by its parts, which is what closes it on its tracker and lets its goal be met.
"""

import asyncio
import logging
from typing import Any

from meta_harness import db
from meta_harness.planner import splits
from meta_harness.tasks.client import TaskApiError, TaskClient

log = logging.getLogger(__name__)


def is_covered(link: dict, by_ref: dict[str, dict]) -> bool:
    """Whether an open issue that was split and also run as one has every part closed: its parts did its work."""
    parts = link.get("split_into") or []
    return bool(
        link["state"] == "open"
        and parts
        and link.get("run_anyway")
        and link.get("task_id")
        and all((by_ref.get(p) or {}).get("state") == "closed" for p in parts)
    )


def _replaced(ref: str) -> None:
    with db.connect(autocommit=True) as conn:
        conn.execute("UPDATE planner.issue_link SET run_anyway = false WHERE ref = %s", (ref,))


async def end_covered(links: list[dict], tasks: TaskClient, errors: list[str]) -> list[str]:
    """End each failed task, with no pull request, of a split issue whose parts are all closed. Returns the
    task ids ended. A task that is running, or has a pull request, is left to end by itself."""
    by_ref = {link["ref"]: link for link in links}
    ended: list[str] = []
    for link in links:
        if not is_covered(link, by_ref):
            continue
        try:
            task: dict[str, Any] = await asyncio.to_thread(tasks.get, link["task_id"])
            if task["status"] != "failed" or task.get("pr_url"):
                continue
            names = ", ".join(splits.short(p) for p in link["split_into"])
            reason = f"the issues its issue was split into are all done ({names}), so nothing is left for it to do"
            await asyncio.to_thread(tasks.set_status, task["id"], "closed", reason)
        except TaskApiError as err:
            errors.append(f"{link['ref']}: {err}")
            continue
        await asyncio.to_thread(_replaced, link["ref"])
        log.info("task %s of %s ended: its parts are all done", task["id"], link["ref"])
        ended.append(task["id"])
    return ended
