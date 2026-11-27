"""A base branch that fails its standing checks gets one task that fixes it.

The run service runs a repo's standing checks on each commit of its base once, and records how each ended
(`base_check_ran`, run_service/base_checks.py). Every run and review is told what fails there, which stops
each one proving it again and fixes nothing. So the goal whose task found the failure gets one fix task for
the repo (docs/specs/base-checks.md):

    POST /v1/goals/{id}/base-fix    start the fix task an operator was asked about

Who starts it follows the goal's autonomy. A goal that asks before it acts lists the failing base in what
needs its operator, with a button that starts the task. Any other goal starts it at its next sweep. One fix
task is open for a repo at a time, whichever goal made it, and a commit is fixed once.
"""

import asyncio
import logging
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from meta_harness import db, outbound
from meta_harness.planner import store
from meta_harness.planner.sweep import goal_tracker
from meta_harness.run_service import provenance
from meta_harness.trackers.base import TrackerError

log = logging.getLogger("meta_harness.planner")
# How many of the newest base check results are read: enough to hold each repo's newest for every check.
RESULTS_READ = 300
# How much of a check's output the fix task's issue carries.
OUTPUT_CHARS = 2000


def failing(events: Any) -> dict[str, dict]:
    """Each repo whose base fails a standing check now, by the newest result for each of its checks: the
    commit, the failing checks with their output, and the goal whose task ran them. A check that passed on a
    later commit no longer fails."""
    page = events.read(0, RESULTS_READ, before_position=2**62, event_type="base_check_ran")["events"]
    newest: dict[tuple[str, str], dict] = {}
    for event in sorted(page, key=lambda e: e["position"]):
        payload = event.get("payload") or {}
        newest[(str(payload.get("repo") or "").lower(), str(payload.get("command") or ""))] = payload
    found: dict[str, dict] = {}
    for (repo, command), payload in newest.items():
        if payload.get("passed") or not repo or not command:
            continue
        entry = found.setdefault(
            repo,
            {
                "repo": str(payload.get("repo")),
                "commit": str(payload.get("commit") or ""),
                "goal_id": str(payload.get("goal_id") or ""),
                "checks": [],
            },
        )
        entry["checks"].append({"command": command, "output": str(payload.get("output") or "")[-OUTPUT_CHARS:]})
    return found


def row_of(repo: str) -> dict | None:
    with db.connect(autocommit=True) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM planner.base_fix WHERE repo = %s", (repo.lower(),))
        return cur.fetchone()


def asked(goal_id: str) -> list[dict]:
    """The failing bases this goal's operator is asked about: found, and no fix task started."""
    with db.connect(autocommit=True) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM planner.base_fix WHERE goal_id = %s AND ref IS NULL ORDER BY found_at", (goal_id,))
        return cur.fetchall()


def _open(ref: str) -> bool:
    """Whether the fix task's issue is still open: its work is not done."""
    with db.connect(autocommit=True) as conn:
        row = conn.execute("SELECT state FROM planner.issue_link WHERE ref = %s", (ref,)).fetchone()
    return bool(row) and row[0] == "open"


def _note(found: dict) -> None:
    with db.connect(autocommit=True) as conn:
        conn.execute(
            "INSERT INTO planner.base_fix (repo, commit_sha, goal_id, checks) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (repo) DO UPDATE SET commit_sha = EXCLUDED.commit_sha, goal_id = EXCLUDED.goal_id, "
            "checks = EXCLUDED.checks, ref = NULL, found_at = now(), started_at = NULL",
            (found["repo"].lower(), found["commit"], found["goal_id"], Jsonb(found["checks"])),
        )


def _forget(repo: str) -> None:
    with db.connect(autocommit=True) as conn:
        conn.execute("DELETE FROM planner.base_fix WHERE repo = %s AND ref IS NULL", (repo.lower(),))


def title(row: dict) -> str:
    commands = ", ".join(check["command"] for check in row["checks"])
    return f"Fix the checks that fail on the base branch: {commands}"[:200]


def body(row: dict) -> str:
    """The fix task's issue: what fails, on which commit, with the end of each check's output, and the checks
    that pass when it is done."""
    lines = [
        f"These checks of the repository fail on its base branch at commit {row['commit_sha'][:12]}, before any "
        "task's change. Every task that runs meets them. Find why each fails and make it pass, changing the "
        "check itself only when the check is what is wrong.",
        "",
    ]
    for check in row["checks"]:
        lines += [
            f"The end of what `{check['command']}` printed:",
            "",
            "```",
            check["output"] or "(no output)",
            "```",
            "",
        ]
    lines += ["## Acceptance checks", ""]
    lines += [f"- `{check['command']}`" for check in row["checks"]]
    return "\n".join(lines)


async def start(goal: dict, row: dict, tracker: Any, events: Any, who: str) -> str:
    """Starts the fix task for the row's repo, as an issue of the goal that its next sweep makes a task. Returns
    the issue's ref.

    Raises:
        TrackerError: The tracker refused the issue. Nothing is started, and the next sweep tries again.
    """
    writer = goal_tracker(tracker, goal)
    # Kept in Muxxy when the goal's tracker carries its own beside it: nobody asked for this in theirs.
    writer = getattr(writer, "own", None) or writer
    where = getattr(writer, "scheme", "") or "github"
    signed = provenance.Provenance(provenance.ISSUE, goal=goal["id"], by=who)
    text = await outbound.for_tracker(body(row), where, outbound.BODY, outbound.FIXED, provenance=signed)
    headed = await outbound.for_tracker(title(row), where, outbound.TITLE, outbound.FIXED)
    labels = [goal["label"]] if goal.get("label") else []
    if labels:
        await writer.ensure_label(row["repo"], labels[0])
    made = await writer.create_issue(row["repo"], headed, text, labels)

    def keep() -> None:
        with db.connect(autocommit=True) as conn:
            store.add_refs(conn, goal["id"], [(made.ref, row["repo"])])
            # Taken in as it is: a fix for the base is not held for its size or for naming no paths.
            store.plan_keep(conn, goal["id"], made.ref, row["repo"], [])
            conn.execute(
                "UPDATE planner.base_fix SET ref = %s, started_at = now() WHERE repo = %s",
                (made.ref, row["repo"].lower()),
            )

    await asyncio.to_thread(keep)
    events.emit(
        "base_fix_started",
        {"goal_id": goal["id"], "repo": row["repo"], "commit": row["commit_sha"], "ref": made.ref},
        actor=who,
        tags={"goal_id": goal["id"]},
    )
    log.info("goal %s: started %s to fix %s's base at %s", goal["id"], made.ref, row["repo"], row["commit_sha"][:12])
    return made.ref


async def look(goal: dict, tracker: Any, events: Any, errors: list[str]) -> list[str]:
    """What a goal's sweep does about a failing base its tasks found: notes it, and starts the fix task unless
    the goal asks before it acts. Returns the refs of the fix tasks started. A base that passes again is
    forgotten, so nothing asks about it."""
    if not hasattr(events, "read"):
        return []
    found = await asyncio.to_thread(failing, events)
    started: list[str] = []
    for asking in await asyncio.to_thread(asked, goal["id"]):
        if asking["repo"] not in found:
            await asyncio.to_thread(_forget, asking["repo"])
    for repo, base in found.items():
        row = await asyncio.to_thread(row_of, repo)
        if row and row["ref"] and await asyncio.to_thread(_open, row["ref"]):
            continue  # one fix task for a repo at a time, whichever goal made it
        if row and row["commit_sha"] == base["commit"] and row["ref"]:
            continue  # this commit's fix was made: the checks have not run on a later one yet
        if row is None or row["commit_sha"] != base["commit"]:
            if base["goal_id"] != goal["id"]:
                continue  # the goal whose task found it takes it
            await asyncio.to_thread(_note, base)
            row = await asyncio.to_thread(row_of, repo)
        if row is None or row["goal_id"] != goal["id"] or goal.get("autonomy") == "ask":
            continue
        try:
            started.append(await start(goal, row, tracker, events, "planner"))
        except (TrackerError, ValueError) as err:
            errors.append(f"{row['repo']}: the task to fix its base could not be started: {err}")
    return started


def register(app: FastAPI, operator: Any, load: Any, tracker: Any, events: Any) -> None:
    @app.post("/v1/goals/{goal_id}/base-fix")
    async def start_base_fix(goal_id: str, who: str = Depends(operator)) -> dict:
        """Start the fix task for each failing base this goal's operator was asked about."""
        goal = load(goal_id)
        rows = await asyncio.to_thread(asked, goal_id)
        if not rows:
            raise HTTPException(
                status_code=409,
                detail="this goal has no failing base waiting for a fix: it was started already, or the base "
                "passes again. Reload the page",
            )
        try:
            refs = [await start(goal, row, tracker, events, who) for row in rows]
        except (TrackerError, ValueError) as err:
            raise HTTPException(
                status_code=502, detail=f"the task to fix the base could not be started: {err}. Press Fix it again"
            ) from err
        return {"goal_id": goal_id, "started": refs}
