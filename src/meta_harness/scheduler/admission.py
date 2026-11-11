"""Admission: which queued tasks start now.

Every MH_ADMISSION_SECONDS the meta-harness scheduler runs one admission pass:

1. It releases the repo-area locks of tasks that are no longer queued or
   running (a task whose pull request is open, `ready`, releases its lock;
   until the meta-harness sees merges, holding the lock to merge would stop
   every other task on the repo), and renews the rest.
2. It takes queued tasks by priority, then age, while fewer tasks hold
   locks than the task policy's max_parallel, read each pass, so a saved
   change applies at the next one.
3. It holds a task whose goal is not active, whose `block` goal has spent
   to its blocking line (its `max_usd` plus its allowed overage; a `warn`
   goal is never held for budget), whose goal already runs its
   `max_parallel` tasks, or whose repo
   area another task holds. A task's areas are its `paths`, from its issue's
   `Paths:` lines. Two areas conflict when they are the same path or one is a
   directory above the other. A task with none takes no area and runs beside
   the others, since each run has its own clone and branch and the merge step
   turns a conflict into a fix run.
4. It locks the task's areas and starts its RunTask workflow.

While the kill switch is draining, as a deploy that replaces the VM sets it,
a pass admits nothing but stops nothing: running tasks finish.

Every task a pass does not start has a reason, saved as its hold (holds.py)
with a kind a page can act on, and recorded as `task_held` when the reason
is new or has changed. The Task API serves a queued task's hold as `held`.

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
from meta_harness.money import usd
from meta_harness.scheduler import holds
from meta_harness.scheduler.holds import Hold

log = logging.getLogger("meta_harness.scheduler")
# An area no current task takes: a lock from before tasks with no paths took no area, which excludes every
# other task on its repo until it is released.
WHOLE_REPO = "*"
# The lock row of a task that takes no area: it only counts the task as running.
NO_AREA = ""
LOCK_TTL = timedelta(hours=6)


@dataclass
class AdmissionResult:
    started: list[str] = field(default_factory=list)
    held: dict[str, str] = field(default_factory=dict)
    released: list[str] = field(default_factory=list)
    canceled: list[str] = field(default_factory=list)


def areas_for(task: dict) -> list[str]:
    """The task's paths, without leading and trailing slashes; none for a task that names no paths."""
    return sorted({p.strip().removeprefix("./").strip("/") for p in task.get("paths") or []} - {""})


def _overlap(a: str, b: str) -> bool:
    """The same path, or one a directory above the other."""
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def conflicts(wanted: list[str], held: list[str]) -> bool:
    wanted = [a for a in wanted if a != NO_AREA]
    held = [a for a in held if a != NO_AREA]
    if not wanted or not held:
        return False
    return WHOLE_REPO in wanted or WHOLE_REPO in held or any(_overlap(a, b) for a in wanted for b in held)


class Admission:
    def __init__(
        self,
        tasks: Any,
        goals: Any,
        events: Any,
        start: Callable[[str], Awaitable[None]],
        max_running: int | Callable[[], int] = 2,
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
        # Each task holding a lock, with its goal (None for a task with no goal), from the last release pass.
        self._running_goal: dict[str, str | None] = {}
        self._canceling: set[str] = set()

    async def pass_once(self) -> AdmissionResult:
        result = AdmissionResult()
        engaged, draining = await asyncio.to_thread(kill_switch_state)
        await self._stop_running(engaged, result)
        await self._hand_over_messages()
        await self._release_and_renew(result)
        queued = await asyncio.to_thread(self.tasks.list, ["queued"], None, 200)
        found: dict[str, Hold] = {}
        if engaged or draining:
            stopped = (
                Hold("kill_switch", "the kill switch is on: no task may start")
                if engaged
                else Hold("draining", "the scheduler is draining for a deploy: no new task starts until it ends")
            )
            # A task being canceled is not waiting to start.
            found = {task["id"]: stopped for task in queued if not task.get("cancel_requested")}
            await self._save_holds(found, result)
            return result
        limit = self.max_running if isinstance(self.max_running, int) else await asyncio.to_thread(self.max_running)
        locked = await asyncio.to_thread(_locks)
        in_flight = {row["task_id"] for row in locked}
        goals: dict[str, dict] = {}
        spend: dict[str, Decimal] = {}
        per_goal: dict[str, int] = {}
        for task_id in in_flight:
            if goal_id := self._running_goal.get(task_id):
                per_goal[goal_id] = per_goal.get(goal_id, 0) + 1
        for task in queued:
            if task["id"] in in_flight:
                continue
            # A deploy's smoke task runs whatever is running: it is tiny, and the deploy waits on it.
            if len(in_flight) >= limit and task.get("task_type") != "smoke":
                found[task["id"]] = Hold(
                    "system_parallel",
                    f"the system already runs {len(in_flight)} tasks, its limit",
                    {"holders": sorted(in_flight)},
                )
                continue
            hold = await self._goal_hold(task, goals, spend)
            if hold is None:
                hold = self._goal_parallel_hold(task, goals, per_goal)
            if hold is None:
                hold = await asyncio.to_thread(_lock, task["id"], task["repo"], areas_for(task))
            if hold is not None:
                found[task["id"]] = hold
                continue
            try:
                await self.start(task["id"])
            except Exception as err:  # noqa: BLE001 - any failure to start: free the area and say why
                await asyncio.to_thread(_unlock, task["id"])
                found[task["id"]] = Hold("start_failed", f"the RunTask workflow did not start: {err}")
                continue
            in_flight.add(task["id"])
            self._running_goal[task["id"]] = task.get("goal_id")
            if task.get("goal_id"):
                per_goal[task["goal_id"]] = per_goal.get(task["goal_id"], 0) + 1
            self.events.emit(
                "task_admitted",
                {"repo": task["repo"], "areas": areas_for(task)},
                task_id=task["id"],
                actor="scheduler",
                event_id=stable_event_id("task_admitted", task["id"]),
            )
            result.started.append(task["id"])
        await self._save_holds(found, result)
        return result

    async def _save_holds(self, found: dict[str, Hold], result: AdmissionResult) -> None:
        """Saves why each task this pass did not start waits, and records task_held for each reason that is new
        or has changed. A task the pass started, or that is queued no longer, has no hold any more."""
        result.held.update({task_id: hold.reason for task_id, hold in found.items()})
        for task_id in await asyncio.to_thread(holds.save, found):
            hold = found[task_id]
            self.events.emit(
                "task_held", {"reason": hold.reason, "kind": hold.kind}, task_id=task_id, actor="scheduler"
            )

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
        running: dict[str, str | None] = {}
        for task_id in {row["task_id"] for row in await asyncio.to_thread(_locks)}:
            try:
                task = await asyncio.to_thread(self.tasks.get, task_id)
            except Exception:  # noqa: BLE001 - keep the lock until the task can be read; it expires on its own
                running[task_id] = self._running_goal.get(task_id)
                continue
            if task["status"] in ("queued", "running", "taken_over"):
                await asyncio.to_thread(_renew, task_id)
                running[task_id] = task.get("goal_id")
            else:
                await asyncio.to_thread(_unlock, task_id)
                result.released.append(task_id)
        self._running_goal = running

    def _goal_parallel_hold(self, task: dict, goals: dict, per_goal: dict[str, int]) -> Hold | None:
        """Why the task waits for its goal's own parallel limit, when the goal sets one and runs that many."""
        goal_id = task.get("goal_id")
        limit = (goals.get(goal_id) or {}).get("max_parallel") if goal_id else None
        if limit is not None and per_goal.get(goal_id, 0) >= int(limit):
            holders = sorted(t for t, g in self._running_goal.items() if g == goal_id)
            return Hold(
                "goal_parallel",
                f"goal {goal_id} already runs {per_goal[goal_id]} tasks, its limit",
                {"goal_id": goal_id, "holders": holders},
            )
        return None

    async def _goal_hold(self, task: dict, goals: dict, spend: dict) -> Hold | None:
        goal_id = task.get("goal_id")
        if not goal_id or self.goals is None:
            return None
        if goal_id not in goals:
            goals[goal_id] = await asyncio.to_thread(self.goals.get, goal_id)
        goal = goals[goal_id]
        # A goal in planning runs one kind of task: the planning task that drafts its issues.
        planning = goal["status"] == "planning" and task.get("task_type") == "plan"
        # A goal stopped by its budget that the budget policy no longer stops: its next sweep makes it active.
        if goal["status"] == "budget_exhausted" and goal.get("budget_policy") == "track":
            return Hold("goal_status", f"goal {goal_id} is starting again", {"goal_id": goal_id, "status": "active"})
        if goal["status"] == "budget_exhausted":
            return Hold("goal_budget", f"goal {goal_id} is stopped by its budget", {"goal_id": goal_id})
        if goal["status"] != "active" and not planning:
            return Hold(
                "goal_status", f"goal {goal_id} is {goal['status']}", {"goal_id": goal_id, "status": goal["status"]}
            )
        # No blocking line: the goal only warns at its budget, or the budget policy only tracks spend.
        if goal.get("max_usd") is None or ("budget_block_at" in goal and goal["budget_block_at"] is None):
            return None
        if goal_id not in spend:
            spend[goal_id] = await asyncio.to_thread(self._goal_spend, goal_id)
        # The planner works the blocking line out; one from before it did blocks at the budget.
        line = Decimal(goal.get("budget_block_at") or goal["max_usd"])
        if spend[goal_id] >= line:
            return Hold(
                "goal_budget",
                f"goal {goal_id} spent {usd(spend[goal_id])} of its {usd(goal['max_usd'])} budget, "
                f"and stops at {usd(line)}",
                {"goal_id": goal_id, "spent_usd": str(spend[goal_id]), "max_usd": str(goal["max_usd"])},
            )
        return None

    def _goal_spend(self, goal_id: str) -> Decimal:
        """What the goal's runs spent: the sum of attempt_finished across its tasks."""
        total = Decimal(0)
        for task in self.tasks.list(None, goal_id, 1000):
            page = self.events.read(0, 5000, task_id=task["id"], event_type="attempt_finished")
            total += sum((Decimal(e["payload"]["spent_usd"]) for e in page["events"]), Decimal(0))
        return total


def kill_switch_engaged() -> bool:
    return kill_switch_state()[0]


def kill_switch_state() -> tuple[bool, bool]:
    """Whether the kill switch is on, and whether the scheduler is draining."""
    with db.connect(autocommit=True) as conn:
        row = conn.execute("SELECT engaged, draining FROM scheduler.kill_switch WHERE id = 1").fetchone()
    return (bool(row and row[0]), bool(row and row[1]))


def kill_switch() -> dict:
    with db.connect(autocommit=True) as conn:
        row = conn.execute(
            "SELECT engaged, changed_by, reason, changed_at, draining FROM scheduler.kill_switch WHERE id = 1"
        ).fetchone()
    return {
        "engaged": row[0],
        "changed_by": row[1],
        "reason": row[2],
        "changed_at": row[3].isoformat(),
        "draining": row[4],
    }


def set_draining(draining: bool, changed_by: str, reason: str | None) -> dict:
    """Starts or ends a drain: no new task is admitted, and running ones finish. engaged is left as it is."""
    with db.connect(autocommit=True) as conn:
        conn.execute(
            "UPDATE scheduler.kill_switch SET draining = %s, changed_by = %s, reason = %s, changed_at = now() "
            "WHERE id = 1",
            (draining, changed_by, reason),
        )
    return kill_switch()


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


def _lock(task_id: str, repo: str, areas: list[str]) -> Hold | None:
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
                return Hold(
                    "repo_area",
                    f"repo area {', '.join(areas)} of {repo} is held by {holder}",
                    {"holders": [holder], "paths": areas, "held_paths": sorted(a for a in holder_areas if a)},
                )
        # A task with no area still holds one row, so it counts as running.
        for area in areas or [NO_AREA]:
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
