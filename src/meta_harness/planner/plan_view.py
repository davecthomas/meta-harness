"""A goal's plan as a person deciding on it needs it: its state, its one decision, and a few figures.

The Plan page shows a plan so that the operator can decide in a few seconds: what is being asked, with the
buttons that answer it; a handful of figures (tasks, repos, the most it may spend); what changed since the
version it is compared with, as counts; and the versions it has had. The first principle this serves is to
reduce decision drag: everything shown helps the decision, and costs as little reading as it allows.

What the plan asks is decided in `actions.plan_decision`, from the goal's records, like every need. This
module works out the figures and the comparison it is given, and reads the plan's versions.
"""

from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from meta_harness.planner import plans

# Who makes a plan version that is the planner's own work.
PLANNER = "planner"
# The sources of a version an operator made or took: what a later version is compared with.
_OPERATORS = ("edited",)


def versions(conn: psycopg.Connection, goal_id: str) -> list[dict[str, Any]]:
    """The plan's versions, oldest first, each with who or what made it and how many tasks it has. The tasks
    themselves are left out: `plans.at` reads one version whole."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT version, source, created_by, created_at, note, stopped_at, jsonb_array_length(drafts) AS tasks, "
            "jsonb_array_length(invalid) AS problems FROM planner.plan WHERE goal_id = %s ORDER BY version",
            (goal_id,),
        )
        rows = cur.fetchall()
    for row in rows:
        row["created_at"] = row["created_at"].isoformat()
    return rows


def baseline(history: list[dict[str, Any]]) -> int | None:
    """The version the latest one is compared with: the last an operator made, else the first of the planner's
    own run of versions. None when the latest is itself the baseline, so there is nothing to compare.

    Every operator sees the same comparison, since it comes from the versions and not from who looked when."""
    if len(history) < 2:
        return None
    latest = history[-1]
    if latest["source"] in _OPERATORS and latest["created_by"] != PLANNER:
        return None
    for row in reversed(history[:-1]):
        if row["source"] in _OPERATORS and row["created_by"] != PLANNER:
            return row["version"]
    return history[0]["version"]


def changes(conn: psycopg.Connection, goal_id: str, plan: dict) -> dict[str, Any] | None:
    """What the plan changes about its baseline, as counts by kind and the tasks of each; None when it is the
    baseline, or nothing differs."""
    since = baseline(versions(conn, goal_id))
    before = plans.at(conn, goal_id, since) if since is not None else None
    if before is None:
        return None
    found = plans.diff(before, plan)
    if not any(found.values()):
        return None
    return {"from_version": since, "lines": plans.diff_lines(found), "diff": found}


def glance(goal: dict, plan: dict, by_size: dict[str, str], parallel: int | None) -> dict[str, Any]:
    """The plan's figures for a glance: how many tasks, in which repos, the most they may spend against the
    goal's budget, how many can start at once, and how many questions are open.

    The most a plan may spend is each task's spend limit for its size, summed: an upper bound, and said as
    one. A task not sized yet counts at the largest size's limit, so the figure never understates."""
    drafts = plan.get("drafts") or []
    limits = {size: Decimal(str(usd)) for size, usd in (by_size or {}).items()}
    most = max(limits.values(), default=Decimal(0))
    may_spend = sum((limits.get(str(d.get("size") or ""), most) for d in drafts), Decimal(0))
    waiting_for_nothing = sum(1 for d in drafts if not d.get("depends_on"))
    return {
        "tasks": len(drafts),
        "repos": sorted({d["repo"] for d in drafts if d.get("repo")}),
        "may_spend_usd": str(may_spend.quantize(Decimal("0.01"))),
        "budget_usd": None if goal.get("max_usd") is None else str(goal["max_usd"]),
        "can_start": min(waiting_for_nothing, parallel) if parallel else waiting_for_nothing,
        "open_questions": len(plan.get("gaps") or []),
        "unsized": sum(1 for d in drafts if not d.get("size")),
    }
