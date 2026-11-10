"""A feature goal's plan: its drafts, whether they hold together, and the versions the planner keeps.

A planning task drafts issues with its create_issue tool. Their drafts are the goal's plan, which an operator
reads, edits and approves before the planner creates any issue. A draft names the others it depends on by
draft_id, and existing issues as owner/repo#number. This module checks a plan and stores its versions; it
calls nothing outside the database.
"""

import re
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DRAFT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
EXISTING_REF = re.compile(r"^(?P<repo>[\w.-]+/[\w.-]+)#(?P<number>\d+)$")
CHECKS_HEADING = re.compile(r"^##\s+acceptance checks\s*$", re.IGNORECASE | re.MULTILINE)
PLAN_COLUMNS = "goal_id, version, drafts, invalid, source, task_id, note, created, created_by, created_at"


def clean(draft: dict[str, Any]) -> dict[str, Any]:
    """A draft with exactly the fields a plan keeps, each of the right kind."""
    return {
        "draft_id": str(draft.get("draft_id") or "").strip(),
        "repo": str(draft.get("repo") or "").strip(),
        "title": str(draft.get("title") or "").strip(),
        "body": str(draft.get("body") or ""),
        "labels": [str(label) for label in draft.get("labels") or [] if str(label).strip()],
        "depends_on": list(
            dict.fromkeys(str(dep).strip() for dep in draft.get("depends_on") or [] if str(dep).strip())
        ),
    }


def latest_drafts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A planning run's drafts from its issue_drafted events, oldest first: the last call for each draft_id is
    the draft, in the order the ids first appeared."""
    drafts: dict[str, dict[str, Any]] = {}
    for event in sorted(events, key=lambda e: e["position"]):
        draft = clean(event.get("payload") or {})
        drafts[draft["draft_id"]] = draft
    return list(drafts.values())


def validate(drafts: list[dict[str, Any]], repos: list[str]) -> list[str]:
    """Why the plan cannot be approved; empty when it can. repos: the goal's.

    A plan needs at least one draft. Each draft has its own draft_id, a title, a body that states its
    acceptance checks, and a repo of the goal. A dependency names another draft or an existing issue, and the
    drafts' dependencies make no cycle.
    """
    if not drafts:
        return ["the plan has no drafts"]
    reasons: list[str] = []
    allowed = {name.lower() for name in repos}
    ids = [d["draft_id"] for d in drafts]
    for draft_id in sorted({i for i in ids if ids.count(i) > 1}):
        reasons.append(f"draft id {draft_id!r} is used by {ids.count(draft_id)} drafts")
    known = set(ids)
    for draft in drafts:
        name = draft["draft_id"] or "(no id)"
        if not DRAFT_ID.match(draft["draft_id"]):
            reasons.append(f"{name}: a draft id is lowercase letters, digits and hyphens, at most 40")
        if not draft["title"]:
            reasons.append(f"{name}: needs a title")
        if not CHECKS_HEADING.search(draft["body"]):
            reasons.append(f"{name}: its body needs a '## Acceptance checks' section")
        if draft["repo"].lower() not in allowed:
            reasons.append(f"{name}: repo {draft['repo'] or '(none)'} is not one of the goal's ({', '.join(repos)})")
        for dep in draft["depends_on"]:
            if dep == draft["draft_id"]:
                reasons.append(f"{name}: depends on itself")
            elif dep not in known and not EXISTING_REF.match(dep):
                reasons.append(f"{name}: depends on {dep}, which is no draft and no issue (owner/repo#number)")
    if cycle := _cycle(drafts):
        reasons.append(f"the drafts depend on each other in a cycle: {' -> '.join(cycle)}")
    return reasons


def _cycle(drafts: list[dict[str, Any]]) -> list[str] | None:
    """One cycle among the drafts' dependencies, as the ids along it, or None."""
    deps = {d["draft_id"]: [x for x in d["depends_on"] if x != d["draft_id"]] for d in drafts}
    done: set[str] = set()

    def visit(node: str, path: list[str]) -> list[str] | None:
        if node in path:
            return [*path[path.index(node) :], node]
        if node in done or node not in deps:
            return None
        for dep in deps[node]:
            if found := visit(dep, [*path, node]):
                return found
        done.add(node)
        return None

    for draft_id in deps:
        if found := visit(draft_id, []):
            return found
    return None


def ordered(drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The drafts with each one after the drafts it depends on, otherwise in the order given. For a valid plan."""
    by_id = {d["draft_id"]: d for d in drafts}
    placed: list[dict[str, Any]] = []
    seen: set[str] = set()

    def place(draft: dict[str, Any]) -> None:
        if draft["draft_id"] in seen:
            return
        seen.add(draft["draft_id"])
        for dep in draft["depends_on"]:
            if dep in by_id:
                place(by_id[dep])
        placed.append(draft)

    for draft in drafts:
        place(draft)
    return placed


def _plan(row: dict) -> dict:
    row["created_at"] = row["created_at"].isoformat()
    return row


def save(
    conn: psycopg.Connection,
    goal_id: str,
    drafts: list[dict[str, Any]],
    invalid: list[str],
    source: str,
    created_by: str,
    task_id: str | None = None,
    note: str | None = None,
) -> dict:
    """Saves the drafts as the goal's next plan version. source: planned (from a planning task) or edited."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id FROM planner.goal WHERE id = %s FOR UPDATE", (goal_id,))
        cur.execute("SELECT coalesce(max(version), 0) AS v FROM planner.plan WHERE goal_id = %s", (goal_id,))
        version = cur.fetchone()["v"] + 1
        cur.execute(
            "INSERT INTO planner.plan (goal_id, version, drafts, invalid, source, task_id, note, created_by) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING {PLAN_COLUMNS}",
            (goal_id, version, Jsonb(drafts), Jsonb(invalid), source, task_id, note, created_by),
        )
        return _plan(cur.fetchone())


def latest(conn: psycopg.Connection, goal_id: str) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {PLAN_COLUMNS} FROM planner.plan WHERE goal_id = %s ORDER BY version DESC LIMIT 1", (goal_id,)
        )
        row = cur.fetchone()
    return _plan(row) if row else None


def from_task(conn: psycopg.Connection, goal_id: str, task_id: str) -> dict | None:
    """The plan version a planning task's drafts were saved as, if they have been."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {PLAN_COLUMNS} FROM planner.plan WHERE goal_id = %s AND task_id = %s", (goal_id, task_id))
        row = cur.fetchone()
    return _plan(row) if row else None


def record_created(conn: psycopg.Connection, goal_id: str, version: int, draft_id: str, ref: str) -> None:
    """Notes the issue created for a draft, as soon as it exists, so an interrupted approval does not make it twice."""
    with conn.transaction():
        conn.execute(
            "UPDATE planner.plan SET created = created || %s WHERE goal_id = %s AND version = %s",
            (Jsonb({draft_id: ref}), goal_id, version),
        )


def set_plan_task(conn: psycopg.Connection, goal_id: str, task_id: str | None) -> None:
    with conn.transaction():
        conn.execute("UPDATE planner.goal SET plan_task_id = %s, updated_at = now() WHERE id = %s", (task_id, goal_id))


def set_approved(conn: psycopg.Connection, goal_id: str) -> None:
    with conn.transaction():
        conn.execute(
            "UPDATE planner.goal SET plan_approved_at = now(), plan_task_id = NULL, updated_at = now() WHERE id = %s",
            (goal_id,),
        )
