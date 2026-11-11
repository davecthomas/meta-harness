"""The supervisor's record of who has each open task.

One row per task that has not ended closed: who has it (rules.OWNERS), which situation decided that, why,
what an operator is asked to do, since when, and what the supervisor's own actions on it came to. The Task
API serves a task's row as its `standing` (`get`, `for_tasks`), read here because the table is the
supervisor's.
"""

from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from meta_harness.supervisor.rules import Decision


@dataclass
class Standing:
    task_id: str
    owner: str
    situation: str
    reason: str
    ask: str
    since: datetime
    attempts: int
    acted_at: datetime | None
    last_error: str


def read(conn: psycopg.Connection) -> dict[str, Standing]:
    """Every task's standing as the last pass left it."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT task_id, owner, situation, reason, coalesce(ask, '') AS ask, since, attempts, acted_at, "
            "coalesce(last_error, '') AS last_error FROM supervisor.standing"
        )
        return {row["task_id"]: Standing(**row) for row in cur.fetchall()}


def save(conn: psycopg.Connection, task_id: str, decision: Decision) -> bool:
    """Save where a pass found the task. A task still in the situation it was in keeps when it entered it and
    what the supervisor did there; one in a new situation starts again. Returns whether the situation is new."""
    row = conn.execute(
        "INSERT INTO supervisor.standing (task_id, owner, situation, reason, ask) VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (task_id) DO UPDATE SET owner = EXCLUDED.owner, reason = EXCLUDED.reason, ask = EXCLUDED.ask, "
        "checked_at = now(), "
        "since = CASE WHEN standing.situation = EXCLUDED.situation THEN standing.since ELSE now() END, "
        "attempts = CASE WHEN standing.situation = EXCLUDED.situation THEN standing.attempts ELSE 0 END, "
        "acted_at = CASE WHEN standing.situation = EXCLUDED.situation THEN standing.acted_at ELSE NULL END, "
        "last_error = CASE WHEN standing.situation = EXCLUDED.situation THEN standing.last_error ELSE NULL END, "
        "situation = EXCLUDED.situation "
        "RETURNING since = checked_at",
        (task_id, decision.owner, decision.situation, decision.reason, decision.ask or None),
    ).fetchone()
    return bool(row[0])


def acted(conn: psycopg.Connection, task_id: str, error: str = "") -> None:
    """Note one action of the supervisor's on the task, and why it failed when it did."""
    conn.execute(
        "UPDATE supervisor.standing SET attempts = attempts + 1, acted_at = now(), last_error = %s WHERE task_id = %s",
        (error or None, task_id),
    )


def forget(conn: psycopg.Connection, keep: list[str]) -> None:
    """Drop the rows of tasks a pass no longer lists: they ended closed, and nothing is left to have."""
    conn.execute("DELETE FROM supervisor.standing WHERE NOT (task_id = ANY(%s))", (keep,))


def for_tasks(conn: psycopg.Connection, task_ids: list[str]) -> dict[str, dict]:
    """Each of these tasks' standing as the Task API serves it. A task with none is left out. An unreadable
    table reads as no standing: a task's page still shows the task."""
    if not task_ids:
        return {}
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT task_id, owner, situation, reason, ask, since, checked_at, attempts, last_error "
                "FROM supervisor.standing WHERE task_id = ANY(%s)",
                (task_ids,),
            )
            rows = cur.fetchall()
    except psycopg.Error:
        return {}
    return {
        row["task_id"]: {
            **{key: row[key] for key in ("owner", "situation", "reason", "ask", "attempts", "last_error")},
            "since": row["since"].isoformat(),
            "checked_at": row["checked_at"].isoformat(),
        }
        for row in rows
    }


def get(conn: psycopg.Connection, task_id: str) -> dict | None:
    return for_tasks(conn, [task_id]).get(task_id)
