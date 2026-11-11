"""The planner's Postgres store: goals and issue links."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from meta_harness.config import store as config_store
from meta_harness.planner import budget

MODALITIES = {"fix", "feature", "new-system"}
# The modalities the planner runs today. new-system needs phases, which are not built.
BUILT_MODALITIES = {"fix", "feature"}
# The modalities whose goal starts with a plan an operator approves before any work (planning.py).
PLANNED_MODALITIES = {"feature"}
GOAL_STATUSES = {"planning", "active", "paused", "met", "stalled", "budget_exhausted", "canceled"}
GOAL_MOVES = {
    # A feature goal plans first: its planning task drafts issues, and an operator approves the plan.
    "planning": {"active", "canceled"},
    "active": {"paused", "met", "stalled", "budget_exhausted", "canceled"},
    "paused": {"active", "planning", "met", "canceled"},
    "stalled": {"active", "met", "canceled"},
    "budget_exhausted": {"active", "canceled"},
    # An operator reopens a met or canceled goal, and the planner takes in its label's open issues again.
    "met": {"active"},
    "canceled": {"active"},
}
GOAL_COLUMNS = (
    "id, created_by, statement, repos, label, modality, modality_source, latitude, auto_merge, max_usd, priority, "
    "status, created_at, updated_at, started_at, closed_at, title, max_parallel, plan_task_id, plan_approved_at, "
    "budget_mode, budget_overage_percent, spent_usd, budget_reached_usd, budget_resume_to"
)
# picked: an operator picked the issue for the goal by ref; false for one its label's sweep took in.
LINK_COLUMNS = (
    "ref, goal_id, repo, task_id, state, depends_on, refused_reason, picked, first_seen_at, last_seen_at, title, labels"
)


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
    # The most of its tasks that run at once. None: the system's, the task policy's max_parallel.
    max_parallel: int | None = None
    # What the budget does when it is reached, and how far past it a block goal may go (budget.py).
    budget_mode: str = budget.DEFAULT_MODE
    budget_overage_percent: int = budget.DEFAULT_OVERAGE_PERCENT


def _check_budget(mode: str | None, overage_percent: int | None) -> None:
    if mode is not None and mode not in budget.MODES:
        raise GoalError(f"budget_mode {mode!r} is not one of {list(budget.MODES)}")
    if overage_percent is not None and overage_percent < 0:
        raise GoalError("budget_overage_percent must be 0 or more")


def new_goal_id() -> str:
    return f"g-{uuid.uuid4().hex[:12]}"


def create(conn: psycopg.Connection, created_by: str, goal: NewGoal) -> dict:
    if goal.modality not in MODALITIES:
        raise GoalError(f"modality {goal.modality!r} is not one of {sorted(MODALITIES)}")
    if goal.modality not in BUILT_MODALITIES:
        raise GoalError(f"the {goal.modality} modality needs phases, which are not built yet; use fix or feature")
    _check_budget(goal.budget_mode, goal.budget_overage_percent)
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"INSERT INTO planner.goal (id, created_by, statement, repos, label, modality, modality_source, "
            f"auto_merge, max_usd, priority, status, started_at, title, max_parallel, budget_mode, "
            f"budget_overage_percent) "
            f"VALUES (%s, %s, %s, %s, %s, %s, 'operator', %s, %s, %s, %s, CASE WHEN %s THEN now() END, %s, %s, "
            f"%s, %s) "
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
                ("planning" if goal.modality in PLANNED_MODALITIES else "active") if goal.start else "paused",
                goal.start and goal.modality not in PLANNED_MODALITIES,
                goal.title,
                goal.max_parallel,
                goal.budget_mode,
                goal.budget_overage_percent,
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


def update(
    conn: psycopg.Connection,
    goal_id: str,
    only_if: str | None = None,
    dropped: list[str] | None = None,
    **fields: Any,
) -> tuple[dict, dict]:
    """Change title, statement, label, repos, auto_merge, priority, max_usd, budget_mode,
    budget_overage_percent, max_parallel or status. Returns (goal, the fields that changed with their old values).

    only_if: change nothing unless the goal's status is this one when its row is locked.

    A goal with no label needs picked issues, ones an operator added by ref, so it still has issues to take
    in: a label goes to None, or a labelless goal's repos change, only when it holds one in its repos.
    Issues it made tasks for stay with it when its label or repos change; the next sweep lists by the new
    ones. An issue it holds with no task yet in a repo it no longer names is let go, so it never becomes a
    task there: its ref is added to dropped, when given.

    A goal's first move to active sets started_at, and the changes then hold started_at too, so the caller
    can tell a goal's first start, which sweeps at once, from a resume.
    """
    allowed = {"title", "statement", "label", "repos", "auto_merge", "priority", "max_usd", "max_parallel", "status"}
    allowed |= {"budget_mode", "budget_overage_percent"}
    if set(fields) - allowed:
        raise GoalError(f"a goal update takes only {sorted(allowed)}")
    _check_budget(fields.get("budget_mode"), fields.get("budget_overage_percent"))
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
        repos_now = [name.lower() for name in fields.get("repos", row["repos"])]
        if ("label" in changed or "repos" in changed) and fields.get("label", row["label"]) is None:
            cur.execute(
                "SELECT 1 FROM planner.issue_link WHERE goal_id = %s AND picked "
                "AND (task_id IS NOT NULL OR lower(repo) = ANY(%s)) LIMIT 1",
                (goal_id, repos_now),
            )
            if cur.fetchone() is None:
                raise GoalError("a goal with no picked issues needs a label: pick its issues first")
        if "repos" in changed:
            cur.execute(
                "DELETE FROM planner.issue_link WHERE goal_id = %s AND task_id IS NULL "
                "AND NOT lower(repo) = ANY(%s) RETURNING ref",
                (goal_id, repos_now),
            )
            if dropped is not None:
                dropped.extend(r["ref"] for r in cur.fetchall())
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


def record_spend(
    conn: psycopg.Connection,
    goal_id: str,
    spent: Decimal,
    resume_to: Decimal | None,
    reached: Decimal | None = None,
) -> None:
    """Save what a look at the goal's spend found: the spend, and the budget a resume would set. reached, when
    given, is the budget goal_budget_reached was just recorded for."""
    with conn.transaction():
        conn.execute(
            "UPDATE planner.goal SET spent_usd = %s, budget_resume_to = %s, "
            "budget_reached_usd = COALESCE(%s, budget_reached_usd) WHERE id = %s",
            (spent, resume_to, reached, goal_id),
        )


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
OPEN_GOAL_STATUSES = ("planning", "active", "paused", "stalled", "budget_exhausted")


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
    title: str | None = None,
    labels: list[str] | None = None,
) -> None:
    with conn.transaction():
        conn.execute(
            "INSERT INTO planner.issue_link "
            "(ref, goal_id, repo, task_id, state, depends_on, refused_reason, title, labels) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (ref) DO UPDATE SET "
            "task_id = COALESCE(EXCLUDED.task_id, planner.issue_link.task_id), state = EXCLUDED.state, "
            "depends_on = EXCLUDED.depends_on, refused_reason = EXCLUDED.refused_reason, "
            "title = COALESCE(EXCLUDED.title, planner.issue_link.title), "
            "labels = COALESCE(EXCLUDED.labels, planner.issue_link.labels), last_seen_at = now()",
            (ref, goal_id, repo, task_id, state, depends_on, refused_reason, title, labels),
        )


def set_link_title(conn: psycopg.Connection, ref: str, title: str) -> None:
    """The issue's title as the tracker has it now, for the goal's board."""
    with conn.transaction():
        conn.execute("UPDATE planner.issue_link SET title = %s WHERE ref = %s", (title, ref))


def set_link_labels(conn: psycopg.Connection, ref: str, labels: list[str]) -> None:
    """The issue's labels as the tracker has them now, for costs by label."""
    with conn.transaction():
        conn.execute("UPDATE planner.issue_link SET labels = %s WHERE ref = %s", (labels, ref))


def task_labels(conn: psycopg.Connection) -> dict[str, list[str]]:
    """For each task made from an issue, that issue's labels. A link stored before the planner kept labels
    has its goal's label in their place, the one label it is sure the issue carried."""
    rows = conn.execute(
        "SELECT l.task_id, COALESCE(l.labels, CASE WHEN g.label IS NULL THEN '{}'::text[] ELSE ARRAY[g.label] END) "
        "FROM planner.issue_link l JOIN planner.goal g ON g.id = l.goal_id WHERE l.task_id IS NOT NULL"
    ).fetchall()
    return {task_id: list(labels) for task_id, labels in rows}


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
                # Its label swept it in first; the operator has now picked it too.
                conn.execute("UPDATE planner.issue_link SET picked = true WHERE ref = %s", (ref,))
                continue
            conn.execute(
                "INSERT INTO planner.issue_link (ref, goal_id, repo, state, depends_on, picked) "
                "VALUES (%s, %s, %s, 'open', '{}', true)",
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
    for key in ("created_at", "updated_at", "started_at", "closed_at", "plan_approved_at"):
        row[key] = row[key].isoformat() if row[key] else None
    row["latitude"] = float(row["latitude"])
    for key in ("max_usd", "spent_usd", "budget_reached_usd", "budget_resume_to"):
        row[key] = str(row[key]) if row[key] is not None else None
    return budget.view(row, config_store.budget_policy())


def _link(row: dict) -> dict:
    for key in ("first_seen_at", "last_seen_at"):
        row[key] = row[key].isoformat()
    return row
