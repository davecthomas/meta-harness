"""The record of every remedy applied: a `supervisor.remedy` row and a `remedy_applied` event per try.

Both carry the same fixed record (remedies/__init__.py), with its three sentences, so whoever reads it (a
page, the planner's update, a model) learns why a task was stuck, what was done and what comes next.
"""

from typing import Any

from psycopg.rows import dict_row

from meta_harness import db

COLUMNS = "task_id, goal_id, situation, remedy, try, of, outcome, because, did, next, error, created_at"


def write(record: dict[str, Any]) -> None:
    with db.connect(autocommit=True) as conn:
        conn.execute(
            "INSERT INTO supervisor.remedy (task_id, goal_id, situation, remedy, try, of, outcome, because, did, "
            "next, error) VALUES (%(task_id)s, %(goal_id)s, %(situation)s, %(remedy)s, %(try)s, %(of)s, "
            "%(outcome)s, %(because)s, %(did)s, %(next)s, %(error)s)",
            record,
        )


def read(task_id: str | None = None, goal_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """The newest first, for a task, a goal, or every task."""
    clauses, params = [], []
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
