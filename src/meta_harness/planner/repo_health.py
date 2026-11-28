"""Each repo's health, for the console's Repos page and the marker beside a repo wherever it is picked.

    GET /v1/repos/health    every repo with an open goal or a registry entry: its base, its checks, the work
                            there, what went live last, and its settings

A repo's own checks and deploy say what its owners count as good and as live (docs/specs/work-with-repo-ci.md).
This reads what the run service and the planner already record of them, and decides nothing: the base's
standing checks (`base_check_ran`), the fix task for a failing base (`planner.base_fix`), the checks re-run
for failing by chance (`remedy_applied`), and the merges a deploy check said were live (`merge_checks`).
Reading it asks the code host only for how long the checks usually take, which is kept ten minutes.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI

from meta_harness import db
from meta_harness.planner import base_fix, repos, store

log = logging.getLogger("meta_harness.planner")
# The goals whose repos are listed: every one that is not over.
OPEN = ("planning", "active", "stalled", "budget_exhausted")
# A task in one of these has ended: it is no longer work in its repo.
ENDED = ("closed", "canceled")
# How far back a re-run counts, and how many of the newest events of a kind are read for the whole page.
RERUN_DAYS = 7
EVENTS_READ = 300
RERUN = "rerun_checks"


def _newest(events: Any, event_type: str) -> list[dict]:
    page = events.read(0, EVENTS_READ, before_position=2**62, event_type=event_type)["events"]
    return sorted(page, key=lambda e: e["position"])


def bases(events: Any) -> dict[str, dict]:
    """Each repo's base as its standing checks last said: the commit, each check with whether it passed and
    where that was said (the sandbox, or the code host's own checks), and the goal whose task ran a failing
    one. The newest result for a check stands."""
    found: dict[str, dict] = {}
    for event in _newest(events, "base_check_ran"):
        payload = event.get("payload") or {}
        repo = str(payload.get("repo") or "").lower()
        command = str(payload.get("command") or "")
        if not repo or not command:
            continue
        base = found.setdefault(repo, {"commit": "", "checks": {}, "goal_id": ""})
        base["commit"] = str(payload.get("commit") or "")
        base["checks"][command] = {
            "command": command,
            "passed": bool(payload.get("passed")),
            "on": "code_host" if payload.get("source") == "code_host" else "sandbox",
        }
        if not payload.get("passed"):
            base["goal_id"] = str(payload.get("goal_id") or "")
    for base in found.values():
        checks = list(base["checks"].values())
        base["checks"] = checks
        base["failing"] = [c["command"] for c in checks if not c["passed"]]
        base["state"] = "failing" if base["failing"] else "passing"
        if not base["failing"]:
            base["goal_id"] = ""
    return found


def reruns(events: Any, repo_of: Callable[[str], str], now: datetime | None = None) -> dict[str, dict[str, int]]:
    """For each repo, how often each check was re-run for failing by chance in the last RERUN_DAYS days."""
    since = (now or datetime.now(UTC)) - timedelta(days=RERUN_DAYS)
    found: dict[str, dict[str, int]] = {}
    for event in _newest(events, "remedy_applied"):
        payload = event.get("payload") or {}
        if payload.get("remedy") != RERUN or not _after(event, since):
            continue
        repo = repo_of(str(event.get("task_id") or ""))
        if not repo:
            continue
        # "tests, lint failed on 1a2b3c4": the checks that were re-run, as the record says them.
        names = str(payload.get("because") or "").split(" failed on ")[0]
        for name in (n.strip() for n in names.split(",")):
            if name:
                counts = found.setdefault(repo.lower(), {})
                counts[name] = counts.get(name, 0) + 1
    return found


def _after(event: dict, since: datetime) -> bool:
    at = str(event.get("time_utc") or "")
    if not at:
        return True
    try:
        return datetime.fromisoformat(at.replace("Z", "+00:00")) >= since
    except ValueError:
        return True


def last_live(events: Any, task_of: Callable[[str], dict | None]) -> dict[str, dict]:
    """For each repo, the newest merge its deploy check said was live: the task, its title and when."""
    found: dict[str, dict] = {}
    for event in _newest(events, "merge_checks"):
        if (event.get("payload") or {}).get("live") is not True:
            continue
        task = task_of(str(event.get("task_id") or ""))
        if not task or not task.get("repo"):
            continue
        found[str(task["repo"]).lower()] = {
            "task_id": task["id"],
            "title": task.get("name") or task.get("title") or "",
            "at": event.get("time_utc") or "",
            "deploy_check": (event.get("payload") or {}).get("deploy_check") or "",
        }
    return found


def _fix(repo: str) -> dict | None:
    """The repo's fix for a failing base: its issue and task once started, or that its goal's operator is asked."""
    row = base_fix.row_of(repo)
    if row is None:
        return None
    if not row["ref"]:
        return {"asked": True, "goal_id": row["goal_id"], "ref": "", "task_id": "", "open": True}
    with db.connect(autocommit=True) as conn:
        link = conn.execute("SELECT task_id, state FROM planner.issue_link WHERE ref = %s", (row["ref"],)).fetchone()
    return {
        "asked": False,
        "goal_id": row["goal_id"],
        "ref": row["ref"],
        "task_id": (link[0] if link else "") or "",
        "open": bool(link) and link[1] == "open",
    }


def gather(tasks: Any, events: Any, usual: dict[str, dict[str, int]]) -> list[dict]:
    """Every repo with an open goal or a registry entry, a failing base first, then by name. usual: each
    repo's checks with how long each usually takes, as far as they are known."""
    with db.connect(autocommit=True) as conn:
        goals = store.list_goals(conn, list(OPEN))
    registered = repos.registry()
    by_task: dict[str, dict | None] = {}
    work: dict[str, dict] = {}
    for goal in goals:
        try:
            its_tasks = tasks.list(None, goal["id"], 500)
        except Exception as err:  # noqa: BLE001 - one goal's tasks unread: its repos are still listed
            log.warning("repo health: could not list goal %s's tasks: %s", goal["id"], err)
            its_tasks = []
        for task in its_tasks:
            by_task[task["id"]] = task
        for name in goal.get("repos") or []:
            entry = work.setdefault(name.lower(), {"name": name, "goals": [], "tasks": 0, "pulls": 0})
            entry["goals"].append({"id": goal["id"], "title": goal.get("title") or (goal.get("statement") or "")[:80]})
            running = [t for t in its_tasks if str(t.get("repo") or "").lower() == name.lower()]
            running = [t for t in running if t.get("status") not in ENDED and t.get("task_type") != "plan"]
            entry["tasks"] += len(running)
            entry["pulls"] += sum(1 for t in running if t.get("pr_url"))

    def task_of(task_id: str) -> dict | None:
        if task_id and task_id not in by_task:
            try:
                by_task[task_id] = tasks.get(task_id)
            except Exception:  # noqa: BLE001 - a task that cannot be read names no repo
                by_task[task_id] = None
        return by_task.get(task_id)

    based = bases(events)
    rerun = reruns(events, lambda task_id: str((task_of(task_id) or {}).get("repo") or ""))
    live = last_live(events, task_of)
    titles = {g["id"]: g.get("title") or (g.get("statement") or "")[:80] for g in goals}
    with db.connect(autocommit=True) as conn:
        alerts = dict(conn.execute("SELECT repo, count(*) FROM planner.security_alert GROUP BY repo").fetchall())
    found = []
    for key in sorted({*work, *registered}):
        entry = registered.get(key)
        name = entry.name if entry else work[key]["name"]
        base = based.get(key)
        times = usual.get(key) or {}
        found.append(
            {
                "repo": name,
                "registered": entry is not None,
                "default_checks": list(entry.default_checks) if entry else [],
                "deploy_check": entry.deploy_check if entry else "",
                "base": {**base, "found_by": titles.get(base["goal_id"], "")} if base else None,
                "fix": _fix(name) if base and base["state"] == "failing" else None,
                # The slowest check: they run side by side, so the wait is its.
                "usual_seconds": max(times.values()) if times else None,
                "reruns": [{"check": c, "times": n} for c, n in sorted((rerun.get(key) or {}).items())],
                "goals": (work.get(key) or {}).get("goals", []),
                "tasks": (work.get(key) or {}).get("tasks", 0),
                "pulls": (work.get(key) or {}).get("pulls", 0),
                "live": live.get(key),
                # The security alerts filed as issues for it (planner/security_alerts.py).
                "alerts": alerts.get(key, 0),
            }
        )
    return sorted(found, key=lambda r: (not (r["base"] and r["base"]["state"] == "failing"), r["repo"].lower()))


def register(
    app: FastAPI,
    operator: Any,
    tasks: Any,
    events: Any,
    usual_seconds: Callable[[str], Awaitable[dict[str, int]]],
) -> None:
    @app.get("/v1/repos/health")
    async def repos_health(who: str = Depends(operator)) -> dict:
        """Each repo with an open goal or a registry entry, a failing base first: how its base stands, how long
        its checks usually take and which were re-run for failing by chance, the work there, the last change
        its deploy check said was live, and its settings. `failing` counts the repos whose base fails."""

        def names() -> list[str]:
            with db.connect(autocommit=True) as conn:
                goals = store.list_goals(conn, list(OPEN))
            return sorted(
                {name for g in goals for name in g.get("repos") or []} | {r.name for r in repos.registry().values()}
            )

        usual: dict[str, dict[str, int]] = {}
        for name in await asyncio.to_thread(names):
            if "/" in name and not name.startswith(("/", ".")):
                usual[name.lower()] = await usual_seconds(name)
        found = await asyncio.to_thread(gather, tasks, events, usual)
        return {"repos": found, "failing": sum(1 for r in found if r["base"] and r["base"]["state"] == "failing")}
