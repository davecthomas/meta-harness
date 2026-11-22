# Created by the meta-harness in run t-45c4a1fc56e9:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""A Fix with AI draft kept for an issue whose task is running (fix_issue.py).

An operator may accept a draft while the issue's task runs. That run read the issue already, so the issue is
left as it is, and the edited draft is kept on the issue's link (`issue_link.fix_kept`). Once the task ends,
fix-draft offers the kept draft again, with no model call, for the next run; an accept that writes the issue
clears it.
"""

from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from meta_harness import db


def keep(goal_id: str, ref: str, parts: list[dict[str, str]], by: str) -> bool:
    """Keep the operator's parts on the goal's link for ref, replacing any kept before. False when the goal
    does not hold ref."""
    kept = {"parts": parts, "by": by, "kept_at": datetime.now(UTC).isoformat()}
    with db.connect(autocommit=True) as conn:
        updated = conn.execute(
            "UPDATE planner.issue_link SET fix_kept = %s WHERE ref = %s AND goal_id = %s AND NOT dropped",
            (Jsonb(kept), ref, goal_id),
        )
        return updated.rowcount == 1


def kept(goal_id: str, ref: str) -> dict[str, Any] | None:
    """The draft kept for ref under the goal, or None."""
    with db.connect(autocommit=True) as conn:
        return kept_in(conn, goal_id, ref)


def kept_in(conn: psycopg.Connection, goal_id: str, ref: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT fix_kept FROM planner.issue_link WHERE ref = %s AND goal_id = %s", (ref, goal_id)
    ).fetchone()
    found = row[0] if row else None
    return found if isinstance(found, dict) and found.get("parts") else None


def clear(goal_id: str, ref: str) -> None:
    """Forget the draft kept for ref: the issue now has what it said."""
    with db.connect(autocommit=True) as conn:
        conn.execute(
            "UPDATE planner.issue_link SET fix_kept = NULL WHERE ref = %s AND goal_id = %s AND fix_kept IS NOT NULL",
            (ref, goal_id),
        )
