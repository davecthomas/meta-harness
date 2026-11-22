"""A goal's plan: what it holds, whether it holds together, and the versions the planner keeps.

A planning task reads the goal and the issues it already has, and records a plan with its tools. A plan holds:

- drafts: the new tasks the planner wrote (create_issue). They exist only in the plan until it is approved.
- items: the planner's decision on each issue the goal already had (plan_issue): keep it, split it into
  drafts that replace it, or leave it out, with the reason, and what it waits on.
- summary and gaps: how the plan reaches the goal, and what it does not cover (plan_summary).
- issues: the issues it was made over, as they were then, so the plan reads the same later.

A task names what it waits on by a draft's id or an existing issue's ref (owner/repo#number is also read, for
a GitHub issue). An operator reads, edits and approves a plan before anything is created or run. This module
checks a plan and stores its versions; it calls nothing outside the database.
"""

import re
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from meta_harness.run_service.code_host import issue_paths

DRAFT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
EXISTING_REF = re.compile(r"^(?P<repo>[\w.-]+/[\w.-]+)#(?P<number>\d+)$")
CHECKS_HEADING = re.compile(r"^##\s+acceptance checks\s*$", re.IGNORECASE | re.MULTILINE)
PLAN_COLUMNS = (
    "goal_id, version, drafts, invalid, source, task_id, note, created, created_by, created_at, items, summary, "
    "gaps, issues, tracked"
)
# What a plan decides for an issue the goal already had.
KEEP, SPLIT, LEAVE_OUT = "keep", "split", "leave_out"
DECISIONS = (KEEP, SPLIT, LEAVE_OUT)


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


def _names(values: Any) -> list[str]:
    return list(dict.fromkeys(str(v).strip() for v in values or [] if str(v).strip()))


def clean_item(item: dict[str, Any]) -> dict[str, Any]:
    """A decision on an existing issue with exactly the fields a plan keeps. An unknown decision is kept as
    given, for validate to name."""
    return {
        "ref": str(item.get("ref") or "").strip(),
        "decision": str(item.get("decision") or KEEP).strip(),
        "reason": str(item.get("reason") or "").strip(),
        "depends_on": _names(item.get("depends_on")),
        "replaced_by": _names(item.get("replaced_by")),
    }


def clean_issue(issue: dict[str, Any]) -> dict[str, str]:
    """An existing issue as a plan remembers it."""
    return {
        "ref": str(issue.get("ref") or ""),
        "repo": str(issue.get("repo") or ""),
        "title": str(issue.get("title") or ""),
    }


def latest_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A planning run's decisions from its issue_planned events: the last call for each ref."""
    items: dict[str, dict[str, Any]] = {}
    for event in sorted(events, key=lambda e: e["position"]):
        item = clean_item(event.get("payload") or {})
        items[item["ref"]] = item
    return list(items.values())


def latest_summary(events: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """A planning run's summary and gaps from its last plan_summarized event; empty when it recorded none."""
    last = max(events, key=lambda e: e["position"], default=None)
    payload = (last or {}).get("payload") or {}
    return str(payload.get("summary") or "").strip(), _names(payload.get("gaps"))


def complete(items: list[dict[str, Any]], issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One decision for each issue the plan was made over, in their order: the planner's, or keep for one it
    said nothing of, since an issue the operator gave the goal runs unless a plan says why not. A decision on
    anything else is kept after them, for validate to refuse."""
    by_ref = {item["ref"]: item for item in items}
    known = [issue["ref"] for issue in issues]
    made = [by_ref.get(ref) or clean_item({"ref": ref}) for ref in known]
    return made + [item for item in items if item["ref"] not in known]


def latest_drafts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A planning run's drafts from its issue_drafted events, oldest first: the last call for each draft_id is
    the draft, in the order the ids first appeared."""
    drafts: dict[str, dict[str, Any]] = {}
    for event in sorted(events, key=lambda e: e["position"]):
        draft = clean(event.get("payload") or {})
        drafts[draft["draft_id"]] = draft
    return list(drafts.values())


def broad_paths(body: str) -> list[str]:
    """The paths a body names that are a whole top-level directory (one part, no file extension): `tests`,
    `src`. A task that names one locks everything under it."""
    return [p for p in issue_paths(body) if "/" not in p and "." not in p]


def broad_path_gaps(drafts: list[dict[str, Any]]) -> list[str]:
    """A gap for each draft that names a whole top-level directory, saying what it costs and how to fix it."""
    return [
        f"{d['title'] or d['draft_id']} names the whole {', '.join(found)} "
        f"{'directory' if len(found) == 1 else 'directories'} as its paths, so every other task that touches "
        f"{'it' if len(found) == 1 else 'them'} waits for this one. Name the files it changes instead"
        for d in drafts
        if (found := broad_paths(d.get("body") or ""))
    ]


def validate(
    drafts: list[dict[str, Any]],
    repos: list[str],
    items: list[dict[str, Any]] | None = None,
    issues: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Why the plan cannot be approved; empty when it can. repos: the goal's. items and issues: its decisions
    on the issues the goal already had, and those issues.

    A plan runs something: at least one draft, or one issue it keeps. Each draft has its own draft_id, a title,
    a body that states its acceptance checks, and a repo of the goal. Each decision is on an issue the plan was
    made over; a split names the drafts that replace the issue. Whatever a task waits on is a draft or an issue
    of the plan that it does not leave out, and nothing waits on itself, directly or around a cycle.
    """
    items, issues = items or [], issues or []
    refs = {issue["ref"] for issue in issues}
    kept = [item for item in items if item["decision"] == KEEP and item["ref"] in refs]
    if not drafts and not kept:
        return ["the plan runs nothing: it has no new task and keeps no issue" if issues else "the plan has no drafts"]
    reasons: list[str] = []
    allowed = {name.lower() for name in repos}
    ids = [d["draft_id"] for d in drafts]
    for draft_id in sorted({i for i in ids if ids.count(i) > 1}):
        reasons.append(f"draft id {draft_id!r} is used by {ids.count(draft_id)} drafts")
    known = set(ids)
    out = {item["ref"]: item["decision"] for item in items if item["decision"] in (SPLIT, LEAVE_OUT)}

    def waits_on(name: str, dep: str) -> None:
        if dep in out:
            how = "leaves out" if out[dep] == LEAVE_OUT else "splits: wait on the tasks that replace it"
            reasons.append(f"{name}: waits on {dep}, which the plan {how}")
        elif dep not in known and dep not in refs and not EXISTING_REF.match(dep):
            reasons.append(f"{name}: depends on {dep}, which is no draft and no issue (owner/repo#number)")

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
            else:
                waits_on(name, dep)
    seen: set[str] = set()
    for item in items:
        ref = item["ref"] or "(no ref)"
        if item["ref"] not in refs:
            reasons.append(f"{ref}: is not one of the goal's issues, so the plan cannot decide on it")
            continue
        if item["ref"] in seen:
            reasons.append(f"{ref}: has more than one decision")
        seen.add(item["ref"])
        if item["decision"] not in DECISIONS:
            reasons.append(f"{ref}: its decision is {item['decision']!r}, not one of {', '.join(DECISIONS)}")
        if item["decision"] == SPLIT:
            if not item["replaced_by"]:
                reasons.append(f"{ref}: is split, so name the new tasks that replace it")
            reasons += [
                f"{ref}: is replaced by {d}, which is no draft of the plan"
                for d in item["replaced_by"]
                if d not in known
            ]
        if item["decision"] != KEEP:
            continue
        for dep in item["depends_on"]:
            if dep == item["ref"]:
                reasons.append(f"{ref}: depends on itself")
            else:
                waits_on(ref, dep)
    if cycle := _cycle(drafts, kept):
        reasons.append(f"the plan's tasks depend on each other in a cycle: {' -> '.join(cycle)}")
    return reasons


def _cycle(drafts: list[dict[str, Any]], kept: list[dict[str, Any]] | None = None) -> list[str] | None:
    """One cycle among the dependencies of the drafts and the kept issues, as the names along it, or None."""
    deps = {d["draft_id"]: [x for x in d["depends_on"] if x != d["draft_id"]] for d in drafts}
    deps.update({i["ref"]: [x for x in i["depends_on"] if x != i["ref"]] for i in kept or []})
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
    items: list[dict[str, Any]] | None = None,
    summary: str = "",
    gaps: list[str] | None = None,
    issues: list[dict[str, Any]] | None = None,
) -> dict:
    """Saves the plan as the goal's next version. source: planned (from a planning task) or edited."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id FROM planner.goal WHERE id = %s FOR UPDATE", (goal_id,))
        cur.execute("SELECT coalesce(max(version), 0) AS v FROM planner.plan WHERE goal_id = %s", (goal_id,))
        version = cur.fetchone()["v"] + 1
        cur.execute(
            "INSERT INTO planner.plan (goal_id, version, drafts, invalid, source, task_id, note, created_by, items, "
            f"summary, gaps, issues) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {PLAN_COLUMNS}",
            (
                goal_id,
                version,
                Jsonb(drafts),
                Jsonb(invalid),
                source,
                task_id,
                note,
                created_by,
                Jsonb(items or []),
                summary,
                Jsonb(gaps or []),
                Jsonb(issues or []),
            ),
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


def set_tracked(conn: psycopg.Connection, goal_id: str, version: int, tracked: bool) -> None:
    """Notes where an approval put the plan's new tasks: the goal's tracker, or the meta-harness alone."""
    with conn.transaction():
        conn.execute(
            "UPDATE planner.plan SET tracked = %s WHERE goal_id = %s AND version = %s", (tracked, goal_id, version)
        )


def set_plan_task(
    conn: psycopg.Connection, goal_id: str, task_id: str | None, issues: list[dict[str, Any]] | None = None
) -> None:
    """Records the goal's planning task in flight, with the issues it was given; None when it has none. When
    one is set, the time is kept as when planning was asked for, and stays after the task ends."""
    with conn.transaction():
        conn.execute(
            "UPDATE planner.goal SET plan_task_id = %s, plan_issues = %s, updated_at = now(), "
            "plan_asked_at = CASE WHEN %s::text IS NULL THEN plan_asked_at ELSE now() END WHERE id = %s",
            (task_id, Jsonb(issues) if task_id and issues is not None else None, task_id, goal_id),
        )


def asked_at(conn: psycopg.Connection, goal_id: str) -> datetime | None:
    """When the goal's planning task in flight, or its last one, was asked for; None when it never had one."""
    row = conn.execute("SELECT plan_asked_at FROM planner.goal WHERE id = %s", (goal_id,)).fetchone()
    return row[0] if row else None


def plan_issues(conn: psycopg.Connection, goal_id: str) -> list[dict[str, Any]]:
    """The issues the goal's planning task in flight was given."""
    row = conn.execute("SELECT plan_issues FROM planner.goal WHERE id = %s", (goal_id,)).fetchone()
    return list(row[0] or []) if row else []


# How long an approval's claim stands with nothing heard from it. An approval that long is taken for dead (the
# planner restarted under it), so the plan can be approved again; what it created is on the plan, and is not
# made twice.
APPROVAL_CLAIM_MINUTES = 15


def claim_approval(conn: psycopg.Connection, goal_id: str) -> bool:
    """Claim the goal's plan for one approval, so two never both create its issues. False while another
    approval holds the claim."""
    with conn.transaction():
        claimed = conn.execute(
            "UPDATE planner.goal SET approving_at = now(), approve_error = NULL WHERE id = %s AND status = 'planning' "
            "AND (approving_at IS NULL OR approving_at < now() - make_interval(mins => %s))",
            (goal_id, APPROVAL_CLAIM_MINUTES),
        )
        return claimed.rowcount == 1


def end_approval(conn: psycopg.Connection, goal_id: str, error: str | None = None) -> None:
    """Let the approval's claim go, keeping why it stopped when it did not finish."""
    with conn.transaction():
        conn.execute("UPDATE planner.goal SET approving_at = NULL, approve_error = %s WHERE id = %s", (error, goal_id))


def approval(conn: psycopg.Connection, goal_id: str) -> dict:
    """Whether an approval of the goal's plan is running now, and why the last one stopped, if it did."""
    row = conn.execute(
        "SELECT approving_at IS NOT NULL AND approving_at >= now() - make_interval(mins => %s), approve_error "
        "FROM planner.goal WHERE id = %s",
        (APPROVAL_CLAIM_MINUTES, goal_id),
    ).fetchone()
    return {"approving": bool(row and row[0]), "approve_error": row[1] if row else None}


def set_approved(conn: psycopg.Connection, goal_id: str) -> None:
    with conn.transaction():
        conn.execute(
            "UPDATE planner.goal SET plan_approved_at = now(), plan_task_id = NULL, updated_at = now() WHERE id = %s",
            (goal_id,),
        )
