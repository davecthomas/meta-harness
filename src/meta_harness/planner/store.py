"""The planner's Postgres store: goals and issue links."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

MODALITIES = {"fix", "feature", "new-system"}
# The modalities the planner runs today. feature and new-system need the planning task.
BUILT_MODALITIES = {"fix"}
GOAL_STATUSES = {"active", "paused", "met", "stalled", "budget_exhausted", "canceled"}
GOAL_MOVES = {
    "active": {"paused", "met", "stalled", "budget_exhausted", "canceled"},
    "paused": {"active", "met", "canceled"},
    "stalled": {"active", "met", "canceled"},
    "budget_exhausted": {"active", "canceled"},
    # An operator reopens a met or canceled goal, and the planner takes in its label's open issues again.
    "met": {"active"},
    "canceled": {"active"},
}
GOAL_COLUMNS = (
    "id, created_by, statement, repos, label, modality, modality_source, latitude, auto_merge, max_usd, priority, "
    "status, created_at, updated_at, started_at, closed_at, title"
)
LINK_COLUMNS = "ref, goal_id, repo, task_id, state, depends_on, refused_reason, first_seen_at, last_seen_at"


class GoalError(ValueError):
    """A request the planner refuses. The message says why."""


class NotFound(LookupError):
    pass


@dataclass
class NewGoal:
    statement: str
    repos: list[str]
    # None: the goal takes only the issues an operator adds by ref.
    label: str | None
    modality: str = "fix"
    auto_merge: bool = False
    max_usd: Decimal | None = None
    priority: int = 0
    # False: save the goal paused and never started, so it takes in no issue until its first resume.
    start: bool = True
    # The goal's short name. None: the planner's model writes one.
    title: str | None = None


def new_goal_id() -> str:
    return f"g-{uuid.uuid4().hex[:12]}"


def create(conn: psycopg.Connection, created_by: str, goal: NewGoal) -> dict:
    if goal.modality not in MODALITIES:
        raise GoalError(f"modality {goal.modality!r} is not one of {sorted(MODALITIES)}")
    if goal.modality not in BUILT_MODALITIES:
        raise GoalError(f"the {goal.modality} modality needs the planning task, which is not built yet; use fix")
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"INSERT INTO planner.goal (id, created_by, statement, repos, label, modality, modality_source, "
            f"auto_merge, max_usd, priority, status, started_at, title) "
            f"VALUES (%s, %s, %s, %s, %s, %s, 'operator', %s, %s, %s, %s, CASE WHEN %s THEN now() END, %s) "
            f"RETURNING {GOAL_COLUMNS}",
            (
                new_goal_id(),
                created_by,
                goal.statement,
                goal.repos,
                goal.label,
                goal.modality,
                goal.auto_merge,
                goal.max_usd,
                goal.priority,
                "active" if goal.start else "paused",
                goal.start,
                goal.title,
            ),
        )
        return _goal(cur.fetchone())


def get(conn: psycopg.Connection, goal_id: str) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {GOAL_COLUMNS} FROM planner.goal WHERE id = %s", (goal_id,))
        row = cur.fetchone()
    if row is None:
        raise NotFound(goal_id)
    return _goal(row)


def list_goals(conn: psycopg.Connection, statuses: list[str] | None = None) -> list[dict]:
    return page_goals(conn, statuses, limit=10_000, offset=0)[0]


def page_goals(conn: psycopg.Connection, statuses: list[str] | None, limit: int, offset: int) -> tuple[list[dict], int]:
    """One page of goals, newest first, and how many match in all."""
    where, params = ("WHERE status = ANY(%s)", [statuses]) if statuses else ("", [])
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {GOAL_COLUMNS} FROM planner.goal {where} ORDER BY created_at DESC, id LIMIT %s OFFSET %s",
            [*params, limit, offset],
        )
        rows = [_goal(row) for row in cur.fetchall()]
        cur.execute(f"SELECT count(*) AS n FROM planner.goal {where}", params)
        return rows, cur.fetchone()["n"]


def search_goals(conn: psycopg.Connection, q: str, limit: int = 10) -> list[dict]:
    """Goals whose title, statement, label or id holds the text, newest first."""
    like = f"%{q}%"
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {GOAL_COLUMNS} FROM planner.goal WHERE title ILIKE %s OR statement ILIKE %s OR label ILIKE %s "
            "OR id ILIKE %s ORDER BY created_at DESC LIMIT %s",
            (like, like, like, like, limit),
        )
        return [_goal(row) for row in cur.fetchall()]


def update(conn: psycopg.Connection, goal_id: str, only_if: str | None = None, **fields: Any) -> tuple[dict, dict]:
    """Change title, statement, auto_merge, priority, max_usd or status. Returns (goal, the fields that changed with their old values).

    only_if: change nothing unless the goal's status is this one when its row is locked.

    A goal's first move to active sets started_at, and the changes then hold started_at too, so the caller
    can tell a goal's first start, which sweeps at once, from a resume.
    """
    allowed = {"title", "statement", "auto_merge", "priority", "max_usd", "status"}
    if set(fields) - allowed:
        raise GoalError(f"a goal update takes only {sorted(allowed)}")
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {GOAL_COLUMNS} FROM planner.goal WHERE id = %s FOR UPDATE", (goal_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFound(goal_id)
        if only_if is not None and row["status"] != only_if:
            return _goal(row), {}
        if "status" in fields and fields["status"] != row["status"]:
            if fields["status"] not in GOAL_MOVES.get(row["status"], set()):
                raise GoalError(f"a {row['status']} goal cannot become {fields['status']}")
        changed = {k: row[k] for k, v in fields.items() if row[k] != v}
        if not changed:
            return _goal(row), {}
        sets = [f"{k} = %s" for k in changed] + ["updated_at = now()"]
        values = [fields[k] for k in changed]
        if "status" in changed and fields["status"] == "active" and row["started_at"] is None:
            sets.append("started_at = now()")
            changed["started_at"] = None
        if fields.get("status") in {"met", "canceled"}:
            sets.append("closed_at = now()")
        elif fields.get("status") == "active":
            sets.append("closed_at = NULL")
        cur.execute(
            f"UPDATE planner.goal SET {', '.join(sets)} WHERE id = %s RETURNING {GOAL_COLUMNS}",
            [*values, goal_id],
        )
        return _goal(cur.fetchone()), changed


def untitled(conn: psycopg.Connection) -> list[dict]:
    """Every goal with no title yet, oldest first."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {GOAL_COLUMNS} FROM planner.goal WHERE title IS NULL ORDER BY created_at")
        return [_goal(row) for row in cur.fetchall()]


def set_title(conn: psycopg.Connection, goal_id: str, title: str) -> bool:
    """Give a goal the title a model wrote, unless it has one by now. Returns whether it was set."""
    with conn.transaction():
        updated = conn.execute(
            "UPDATE planner.goal SET title = %s, updated_at = now() WHERE id = %s AND title IS NULL", (title, goal_id)
        )
        return updated.rowcount == 1


def link(conn: psycopg.Connection, ref: str) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {LINK_COLUMNS} FROM planner.issue_link WHERE ref = %s", (ref,))
        row = cur.fetchone()
    return _link(row) if row else None


# A goal in one of these still holds its issues: an issue it holds is not offered to another goal.
OPEN_GOAL_STATUSES = ("active", "paused", "stalled", "budget_exhausted")


def holders(conn: psycopg.Connection, refs: list[str]) -> dict[str, str]:
    """For each ref an open goal holds, that goal's id."""
    if not refs:
        return {}
    rows = conn.execute(
        "SELECT l.ref, l.goal_id FROM planner.issue_link l JOIN planner.goal g ON g.id = l.goal_id "
        "WHERE l.ref = ANY(%s) AND g.status = ANY(%s)",
        (refs, list(OPEN_GOAL_STATUSES)),
    ).fetchall()
    return {ref: goal_id for ref, goal_id in rows}


def links(conn: psycopg.Connection, goal_id: str) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {LINK_COLUMNS} FROM planner.issue_link WHERE goal_id = %s ORDER BY first_seen_at", (goal_id,)
        )
        return [_link(row) for row in cur.fetchall()]


def save_link(
    conn: psycopg.Connection,
    ref: str,
    goal_id: str,
    repo: str,
    state: str,
    depends_on: list[str],
    task_id: str | None = None,
    refused_reason: str | None = None,
) -> None:
    with conn.transaction():
        conn.execute(
            "INSERT INTO planner.issue_link (ref, goal_id, repo, task_id, state, depends_on, refused_reason) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (ref) DO UPDATE SET "
            "task_id = COALESCE(EXCLUDED.task_id, planner.issue_link.task_id), state = EXCLUDED.state, "
            "depends_on = EXCLUDED.depends_on, refused_reason = EXCLUDED.refused_reason, last_seen_at = now()",
            (ref, goal_id, repo, task_id, state, depends_on, refused_reason),
        )


def add_refs(conn: psycopg.Connection, goal_id: str, refs: list[tuple[str, str]]) -> list[str]:
    """Hold these (ref, repo) pairs for the goal, for its sweep to take in. Returns the refs it now holds that
    it did not. A ref another goal holds, in any status, is refused, naming that goal."""
    added = []
    with conn.transaction():
        for ref, repo in refs:
            row = conn.execute(
                "SELECT l.goal_id, g.status FROM planner.issue_link l JOIN planner.goal g ON g.id = l.goal_id "
                "WHERE l.ref = %s FOR UPDATE OF l",
                (ref,),
            ).fetchone()
            if row and row[0] != goal_id:
                raise GoalError(f"{ref} is held by goal {row[0]}, which is {row[1]}")
            if row:
                continue
            conn.execute(
                "INSERT INTO planner.issue_link (ref, goal_id, repo, state, depends_on) VALUES (%s, %s, %s, 'open', '{}')",
                (ref, goal_id, repo),
            )
            added.append(ref)
    return added


def drop_ref(conn: psycopg.Connection, goal_id: str, ref: str) -> None:
    """Let go of an issue the goal holds and has made no task for yet."""
    with conn.transaction():
        row = conn.execute(
            "SELECT task_id FROM planner.issue_link WHERE ref = %s AND goal_id = %s FOR UPDATE", (ref, goal_id)
        ).fetchone()
        if row is None:
            raise NotFound(ref)
        if row[0]:
            raise GoalError(f"{ref} already has task {row[0]}: cancel the task instead")
        conn.execute("DELETE FROM planner.issue_link WHERE ref = %s", (ref,))


def set_link_state(conn: psycopg.Connection, ref: str, state: str) -> None:
    with conn.transaction():
        conn.execute("UPDATE planner.issue_link SET state = %s, last_seen_at = now() WHERE ref = %s", (state, ref))


def _goal(row: dict) -> dict:
    for key in ("created_at", "updated_at", "started_at", "closed_at"):
        row[key] = row[key].isoformat() if row[key] else None
    row["latitude"] = float(row["latitude"])
    row["max_usd"] = str(row["max_usd"]) if row["max_usd"] is not None else None
    return row


def _link(row: dict) -> dict:
    for key in ("first_seen_at", "last_seen_at"):
        row[key] = row[key].isoformat()
    return row
