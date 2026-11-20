"""The record of every remedy applied: a `supervisor.remedy` row and a `remedy_applied` event per try.

Both carry the same fixed record (remedies/__init__.py), with its three sentences, so whoever reads it (a
page, the planner's update, a model) learns why a task was stuck, what was done and what comes next.
"""

from typing import Any

from psycopg.rows import dict_row

from meta_harness import db

COLUMNS = "task_id, ref, goal_id, situation, remedy, try, of, outcome, because, did, next, error, finding, created_at"


def write(record: dict[str, Any]) -> None:
    with db.connect(autocommit=True) as conn:
        conn.execute(
            "INSERT INTO supervisor.remedy (task_id, ref, goal_id, situation, remedy, try, of, outcome, because, did, "
            "next, error, finding) VALUES (%(task_id)s, %(ref)s, %(goal_id)s, %(situation)s, %(remedy)s, %(try)s, "
            "%(of)s, %(outcome)s, %(because)s, %(did)s, %(next)s, %(error)s, %(finding)s)",
            {"ref": None, "task_id": None, "finding": None, **record},
        )


def read(
    task_id: str | None = None, goal_id: str | None = None, limit: int = 100, ref: str | None = None
) -> list[dict[str, Any]]:
    """The newest first, for a task, an issue (`ref`), a goal, or every one."""
    clauses, params = [], []
    if ref:
        clauses.append("ref = %s")
        params.append(ref)
    if task_id:
        clauses.append("task_id = %s")
        params.append(task_id)
    if goal_id:
        clauses.append("goal_id = %s")
        params.append(goal_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db.connect(autocommit=True) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {COLUMNS} FROM supervisor.remedy {where} ORDER BY id DESC LIMIT %s", (*params, limit))
        rows = cur.fetchall()
    for row in rows:
        row["created_at"] = row["created_at"].isoformat()
    return rows


def tries(task_id: str, remedy: str) -> int:
    """How many times the remedy was applied to the task: its tries that did not fail to be applied."""
    with db.connect(autocommit=True) as conn:
        found = conn.execute(
            "SELECT count(*) FROM supervisor.remedy WHERE task_id = %s AND remedy = %s AND outcome <> 'failed'",
            (task_id, remedy),
        ).fetchone()
    return int(found[0])


def tries_for_issue(ref: str, remedy: str) -> int:
    """How many times the remedy was tried on the issue, those that failed included: a remedy that keeps
    failing on an issue stops at its limit instead of trying each pass."""
    with db.connect(autocommit=True) as conn:
        found = conn.execute(
            "SELECT count(*) FROM supervisor.remedy WHERE ref = %s AND remedy = %s", (ref, remedy)
        ).fetchone()
    return int(found[0])


def finding(task_id: str, remedy: str, because: str) -> str | None:
    """What the remedy found for the task the last time it ran on this same reason; None when it has not."""
    with db.connect(autocommit=True) as conn:
        found = conn.execute(
            "SELECT finding FROM supervisor.remedy WHERE task_id = %s AND remedy = %s AND because = %s "
            "ORDER BY id DESC LIMIT 1",
            (task_id, remedy, because),
        ).fetchone()
    return found[0] if found else None
