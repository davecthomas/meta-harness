"""Admission: which queued tasks start now.

Every MH_ADMISSION_SECONDS the meta-harness scheduler runs one admission pass:

1. It releases the repo-area locks of tasks that are no longer queued or
   running (a task whose pull request is open, `ready`, releases its lock;
   until the meta-harness sees merges, holding the lock to merge would stop
   every other task on the repo), and renews the rest.
2. It takes queued tasks by priority, then age, while fewer than
   MH_MAX_RUNNING tasks hold locks.
3. It holds a task whose goal is not active, whose goal has spent its
   `max_usd`, or whose repo area another task holds. A task names its area
   with `paths` (their top-level directories); a task with none takes the
   whole repo.
4. It locks the task's areas and starts its RunTask workflow.

Blocked tasks are not queued: the planner queues them once their
dependencies close.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any, Awaitable, Callable

from meta_harness import db
from meta_harness.events.client import stable_event_id

log = logging.getLogger("meta_harness.scheduler")
WHOLE_REPO = "*"
LOCK_TTL = timedelta(hours=6)


@dataclass
class AdmissionResult:
    started: list[str] = field(default_factory=list)
    held: dict[str, str] = field(default_factory=dict)
    released: list[str] = field(default_factory=list)
    canceled: list[str] = field(default_factory=list)


def areas_for(task: dict) -> list[str]:
    tops = sorted({p.strip("/").split("/", 1)[0] for p in task.get("paths") or [] if p.strip("/")})
    return tops or [WHOLE_REPO]


def conflicts(wanted: list[str], held: list[str]) -> bool:
    if not wanted or not held:
        return False
    return WHOLE_REPO in wanted or WHOLE_REPO in held or bool(set(wanted) & set(held))


class Admission:
    def __init__(
        self,
        tasks: Any,
        goals: Any,
        events: Any,
        start: Callable[[str], Awaitable[None]],
        max_running: int = 2,
        cancel: Callable[[str], Awaitable[None]] | None = None,
        signal: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> None:
        self.tasks = tasks
        self.goals = goals
        self.events = events
        self.start = start
        self.cancel = cancel
        self.signal = signal
        self.max_running = max_running
        self._last_hold: dict[str, str] = {}
        self._canceling: set[str] = set()

    async def pass_once(self) -> AdmissionResult:
        result = AdmissionResult()
        engaged = await asyncio.to_thread(kill_switch_engaged)
        await self._stop_running(engaged, result)
        await self._hand_over_messages()
        await self._release_and_renew(result)
        if engaged:
            return result
        locked = await asyncio.to_thread(_locks)
        in_flight = {row["task_id"] for row in locked}
        queued = await asyncio.to_thread(self.tasks.list, ["queued"], None, 200)
        goals: dict[str, dict] = {}
        spend: dict[str, Decimal] = {}
        for task in queued:
            if len(in_flight) >= self.max_running:
                break
            if task["id"] in in_flight:
                continue
            reason = await self._goal_hold(task, goals, spend)
            if reason is None:
                reason = await asyncio.to_thread(_lock, task["id"], task["repo"], areas_for(task))
            if reason is not None:
                self._hold(task, reason, result)
                continue
            try:
                await self.start(task["id"])
            except Exception as err:  # noqa: BLE001 - any failure to start: free the area and say why
                await asyncio.to_thread(_unlock, task["id"])
                self._hold(task, f"the RunTask workflow did not start: {err}", result)
                continue
            in_flight.add(task["id"])
            self._last_hold.pop(task["id"], None)
            self.events.emit(
                "task_admitted",
                {"repo": task["repo"], "areas": areas_for(task)},
                task_id=task["id"],
                actor="scheduler",
                event_id=stable_event_id("task_admitted", task["id"]),
            )
            result.started.append(task["id"])
        return result

    async def _hand_over_messages(self) -> None:
        """Hands each operator message to its task's RunTask workflow as a signal, then marks it delivered. One
        that cannot be sent waits for the next pass."""
        if self.signal is None or not hasattr(self.tasks, "pending_messages"):
            return
        for message in await asyncio.to_thread(self.tasks.pending_messages):
            try:
                await self.signal(
                    message["task_id"],
                    {"message_id": message["id"], "text": message["body"], "when": message["deliver"]},
                )
            except Exception as err:  # noqa: BLE001 - try again next pass
                log.warning("could not hand message %s to %s: %s", message["id"], message["task_id"], err)
                continue
            await asyncio.to_thread(self.tasks.mark_delivered, message["id"])

    async def _stop_running(self, engaged: bool, result: AdmissionResult) -> None:
        """Cancels the RunTask workflow of each running task that has a cancel request, or of every
        running task while the kill switch is on. The workflow stops pi and marks the task canceled."""
        if self.cancel is None:
            return
        running = await asyncio.to_thread(self.tasks.list, ["running", "queued"], None, 1000)
        for task in running:
            stop = engaged and task["status"] == "running" or task.get("cancel_requested")
            if not stop or task["id"] in self._canceling:
                continue
            try:
                await self.cancel(task["id"])
            except Exception as err:  # noqa: BLE001 - try again next pass
                log.warning("could not cancel %s: %s", task["id"], err)
                continue
            self._canceling.add(task["id"])
            result.canceled.append(task["id"])

    async def _release_and_renew(self, result: AdmissionResult) -> None:
        for task_id in {row["task_id"] for row in await asyncio.to_thread(_locks)}:
            try:
                task = await asyncio.to_thread(self.tasks.get, task_id)
            except Exception:  # noqa: BLE001 - keep the lock until the task can be read; it expires on its own
                continue
            if task["status"] in ("queued", "running", "taken_over"):
                await asyncio.to_thread(_renew, task_id)
            else:
                await asyncio.to_thread(_unlock, task_id)
                result.released.append(task_id)

    async def _goal_hold(self, task: dict, goals: dict, spend: dict) -> str | None:
        goal_id = task.get("goal_id")
        if not goal_id or self.goals is None:
            return None
        if goal_id not in goals:
            goals[goal_id] = await asyncio.to_thread(self.goals.get, goal_id)
        goal = goals[goal_id]
        if goal["status"] != "active":
            return f"goal {goal_id} is {goal['status']}"
        if goal.get("max_usd") is None:
            return None
        if goal_id not in spend:
            spend[goal_id] = await asyncio.to_thread(self._goal_spend, goal_id)
        if spend[goal_id] >= Decimal(goal["max_usd"]):
            return f"goal {goal_id} spent ${spend[goal_id]} of its ${goal['max_usd']} budget"
        return None

    def _goal_spend(self, goal_id: str) -> Decimal:
        """What the goal's runs spent: the sum of attempt_finished across its tasks."""
        total = Decimal(0)
        for task in self.tasks.list(None, goal_id, 1000):
            page = self.events.read(0, 5000, task_id=task["id"], event_type="attempt_finished")
            total += sum((Decimal(e["payload"]["spent_usd"]) for e in page["events"]), Decimal(0))
        return total

    def _hold(self, task: dict, reason: str, result: AdmissionResult) -> None:
        result.held[task["id"]] = reason
        if self._last_hold.get(task["id"]) != reason:
            self._last_hold[task["id"]] = reason
            self.events.emit("task_held", {"reason": reason}, task_id=task["id"], actor="scheduler")


def kill_switch_engaged() -> bool:
    with db.connect(autocommit=True) as conn:
        row = conn.execute("SELECT engaged FROM scheduler.kill_switch WHERE id = 1").fetchone()
    return bool(row and row[0])


def kill_switch() -> dict:
    with db.connect(autocommit=True) as conn:
        row = conn.execute(
            "SELECT engaged, changed_by, reason, changed_at FROM scheduler.kill_switch WHERE id = 1"
        ).fetchone()
    return {"engaged": row[0], "changed_by": row[1], "reason": row[2], "changed_at": row[3].isoformat()}


def set_kill_switch(engaged: bool, changed_by: str, reason: str | None) -> dict:
    with db.connect(autocommit=True) as conn:
        conn.execute(
            "UPDATE scheduler.kill_switch SET engaged = %s, changed_by = %s, reason = %s, changed_at = now() WHERE id = 1",
            (engaged, changed_by, reason),
        )
    return kill_switch()


def _locks() -> list[dict]:
    with db.connect(autocommit=True) as conn:
        rows = conn.execute("SELECT task_id, repo, area FROM scheduler.repo_area_lock").fetchall()
    return [{"task_id": r[0], "repo": r[1], "area": r[2]} for r in rows]


def _lock(task_id: str, repo: str, areas: list[str]) -> str | None:
    """Locks the task's areas, or returns why it cannot: the task holding a conflicting area."""
    repo = repo.lower()
    with db.connect(autocommit=True) as conn, conn.transaction():
        # One admission pass at a time per repo, so two schedulers never lock the same area.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (repo,))
        conn.execute("DELETE FROM scheduler.repo_area_lock WHERE expires_at < now()")
        held: dict[str, list[str]] = {}
        for holder, area in conn.execute(
            "SELECT task_id, area FROM scheduler.repo_area_lock WHERE repo = %s AND task_id <> %s", (repo, task_id)
        ).fetchall():
            held.setdefault(holder, []).append(area)
        for holder, holder_areas in held.items():
            if conflicts(areas, holder_areas):
                return f"repo area {', '.join(areas)} of {repo} is held by {holder}"
        for area in areas:
            conn.execute(
                "INSERT INTO scheduler.repo_area_lock (task_id, repo, area, expires_at) VALUES (%s, %s, %s, now() + %s) "
                "ON CONFLICT (task_id, area) DO UPDATE SET expires_at = EXCLUDED.expires_at",
                (task_id, repo, area, LOCK_TTL),
            )
    return None


def _renew(task_id: str) -> None:
    with db.connect(autocommit=True) as conn:
        conn.execute(
            "UPDATE scheduler.repo_area_lock SET expires_at = now() + %s WHERE task_id = %s", (LOCK_TTL, task_id)
        )


def _unlock(task_id: str) -> None:
    with db.connect(autocommit=True) as conn:
        conn.execute("DELETE FROM scheduler.repo_area_lock WHERE task_id = %s", (task_id,))
