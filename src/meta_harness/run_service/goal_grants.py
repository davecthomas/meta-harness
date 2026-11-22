"""What a goal's budget has promised to runs that have not finished.

A run is granted a share of what its goal has left before it starts, and its spend is recorded only when it
ends. Counting finished spend alone, tasks that asked one after another were each promised the same money, and
together passed the goal's blocking line. So each grant is written down here when it is made, counted against
the goal until the run is recorded, and the read and the grant happen under one lock per goal.

A grant counts only while its task is running and for a day at most: a row left behind by a worker that died
never holds a goal's budget for long.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from decimal import Decimal

import psycopg

from meta_harness import db

# How long a grant counts with no run recorded for it: longer than any run lasts.
GRANT_HOURS = 24


@contextmanager
def locked(goal_id: str) -> Iterator[psycopg.Connection]:
    """A transaction holding the goal's grant lock: one task at a time reads what is left and takes its share."""
    with db.connect() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"goal-grant:{goal_id}",))
        yield conn


def outstanding(conn: psycopg.Connection, goal_id: str, running: Iterable[str]) -> Decimal:
    """What the goal has granted to those running tasks' unfinished runs."""
    tasks = list(running)
    if not tasks:
        return Decimal(0)
    row = conn.execute(
        "SELECT coalesce(sum(usd), 0) FROM run_service.goal_grant WHERE goal_id = %s AND task_id = ANY(%s) "
        "AND granted_at > now() - make_interval(hours => %s)",
        (goal_id, tasks, GRANT_HOURS),
    ).fetchone()
    return Decimal(row[0])


def reserve(conn: psycopg.Connection, task_id: str, goal_id: str, usd: Decimal) -> None:
    """Writes down the task's grant, in place of any earlier one. Nothing granted: no row."""
    if usd <= 0:
        conn.execute("DELETE FROM run_service.goal_grant WHERE task_id = %s", (task_id,))
        return
    conn.execute(
        "INSERT INTO run_service.goal_grant (task_id, goal_id, usd) VALUES (%s, %s, %s) "
        "ON CONFLICT (task_id) DO UPDATE SET goal_id = EXCLUDED.goal_id, usd = EXCLUDED.usd, granted_at = now()",
        (task_id, goal_id, usd),
    )


def release(task_id: str) -> None:
    """The task's run was recorded: what it spent is counted as spent now, so its grant no longer is."""
    with db.connect(autocommit=True) as conn:
        conn.execute("DELETE FROM run_service.goal_grant WHERE task_id = %s", (task_id,))
