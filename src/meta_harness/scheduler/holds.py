"""Why each queued task waits: the scheduler's record of the tasks it holds.

An admission pass holds a queued task for one of a few reasons, each with a kind a page can act on without
reading the reason's words:

- `goal_budget`: its goal has spent to its blocking line, or is stopped by its budget
- `goal_status`: its goal is paused, planning or closed
- `goal_parallel`: its goal already runs its own parallel limit
- `system_parallel`: the system already runs its parallel limit
- `repo_area`: another task holds its repo area
- `kill_switch`, `draining`: no task may start
- `start_failed`: its RunTask workflow did not start

Each pass saves what it found (`save`): one row per held task, with when the task began waiting for that reason
and when a pass last looked. A task that starts, or is no longer queued, loses its row. The rows survive a
restart, so a restart records no `task_held` event again. The Task API serves a task's row as its `held`
(`get`, `for_tasks`), read here because the table is the scheduler's.
"""

from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from meta_harness import db

KINDS = (
    "goal_budget",
    "goal_status",
    "goal_parallel",
    "system_parallel",
    "repo_area",
    "kill_switch",
    "draining",
    "start_failed",
)


@dataclass
class Hold:
    """Why an admission pass did not start a task. detail names what a page links to: goal_id, the tasks
    holding the places (holders), the paths two tasks share (paths), a goal's status."""

    kind: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


def save(held: dict[str, Hold]) -> list[str]:
    """Save one pass's holds: every task the pass did not start, with why. Any other task loses its row: it
    started, or is queued no longer. Returns the tasks whose reason is new or changed, for task_held."""
    changed: list[str] = []
    with db.connect(autocommit=True) as conn, conn.transaction():
        known = {
            row[0]: row[1]
            for row in conn.execute("SELECT task_id, reason FROM scheduler.task_hold FOR UPDATE").fetchall()
        }
        gone = [task_id for task_id in known if task_id not in held]
        if gone:
            conn.execute("DELETE FROM scheduler.task_hold WHERE task_id = ANY(%s)", (gone,))
        for task_id, hold in held.items():
            if known.get(task_id) == hold.reason:
                conn.execute("UPDATE scheduler.task_hold SET last_checked = now() WHERE task_id = %s", (task_id,))
                continue
            conn.execute(
                "INSERT INTO scheduler.task_hold (task_id, kind, reason, detail) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (task_id) DO UPDATE SET kind = EXCLUDED.kind, reason = EXCLUDED.reason, "
                "detail = EXCLUDED.detail, since = now(), last_checked = now()",
                (task_id, hold.kind, hold.reason, Jsonb(hold.detail)),
            )
            changed.append(task_id)
    return changed


def for_tasks(conn: psycopg.Connection, task_ids: list[str]) -> dict[str, dict]:
    """Each of these tasks' hold as the Task API serves it: {reason, kind, since, last_checked, detail}. A task
    with none is left out. An unreadable table reads as no holds: a task's page still shows the task."""
    if not task_ids:
        return {}
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT task_id, reason, kind, since, last_checked, detail FROM scheduler.task_hold "
                "WHERE task_id = ANY(%s)",
                (task_ids,),
            )
            rows = cur.fetchall()
    except psycopg.Error:
        return {}
    return {
        row["task_id"]: {
            "reason": row["reason"],
            "kind": row["kind"],
            "since": row["since"].isoformat(),
            "last_checked": row["last_checked"].isoformat(),
            "detail": row["detail"],
        }
        for row in rows
    }


def get(conn: psycopg.Connection, task_id: str) -> dict | None:
    return for_tasks(conn, [task_id]).get(task_id)
