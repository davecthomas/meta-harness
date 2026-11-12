"""One pass of the supervisor: look at every open task, decide who has it, and move the ones no one has.

A pass reads each task that has not ended closed, gathers where it stands (its row, its RunTask workflow in
Temporal, its pull request on the code host, its goal), asks rules.decide who has it, and saves the answer
(standing.py). Where the answer is the supervisor, the pass acts:

    recover               a task with no workflow behind it goes back to the queue, to resume where its pull
                          request stands, with what it spent carried over; a task asked to cancel is canceled
    end_at_pull_request   a task whose pull request merged or closed, which no goal's sweep covers, is closed
                          or canceled
    merge                 a ready task of a goal that merges by itself, whose pull request the code host
                          would take or that conflicts with its base, goes back for its merge; the merge
                          step resolves a conflict in a fix run first
    review                one whose checks failed after its review goes back through its review loop
    wait                  one whose checks have not reported is looked at again next pass
    retry                 a failed task whose failure was the system's (failures.py) runs again, after a
                          wait that grows with each try; a review loop the system stopped goes back the same
    cancel_for_goal       a task that has not started, of a goal that ended, is canceled

Every action is safe to repeat: the task service refuses a move that no longer applies. A task is acted on
only after it has stood in its situation for GRACE, so a status write still on its way is not taken for a
lost one, and an action that fails is tried again with a longer wait each time. Nothing a pass cannot read
(Temporal, the code host, one goal) stops the pass: that task's facts say unknown, and no one is moved on
an unknown.
"""

import asyncio
import dataclasses
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from meta_harness import db
from meta_harness.planner import cost_facts, store as goal_store
from meta_harness.planner import sweep
from meta_harness.run_service import code_host
from meta_harness.run_service.code_host import CodeHostError
from meta_harness.supervisor import failures, pulls, standing
from meta_harness.supervisor.rules import OPEN_STATUSES, SWEPT, Decision, Facts, decide
from meta_harness.tasks.store import MAX_DRIVES
from meta_harness.tasks.client import TaskApiError, TaskClient

log = logging.getLogger("meta_harness.supervisor")

# How long a task stands with no one having it before the supervisor recovers it. A workflow that just
# ended may still be writing the task's last status, and one just started has not marked it running yet.
GRACE = timedelta(minutes=3)
# The wait before an action that failed is tried again: doubled each time, up to the longest.
RETRY_FIRST = timedelta(minutes=1)
RETRY_LONGEST = timedelta(hours=1)
# How long a read of a pull request stands, so a pass does not read every parked task's each minute.
PULL_SECONDS = 300.0
# How long a read of how a pull request stands for a merge is kept: shorter, since a merge waits on it.
MERGEABLE_SECONDS = 120.0
# How long after a failure on the system's side the supervisor retries the task: by how often it has already
# sent the task back. An outage that outlasts the first wait is met by a longer one.
RETRY_WAITS = (timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2))
# How long after a failure the supervisor still retries it by itself. An older failure is an operator's: the
# supervisor does not start work again that failed days ago and that no one has asked for since.
RETRY_WINDOW = timedelta(hours=24)
# The longest the supervisor waits on a pull request's checks before it asks an operator to look.
WAIT_LONGEST = timedelta(hours=24)


class Events(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any] | None = None, **fields: Any) -> str: ...

    def read(self, from_position: int = 0, limit: int = 500, **filters: str) -> dict[str, Any]: ...


class Workflows(Protocol):
    async def state(self, task_id: str) -> str:
        """running, ended, none or unknown (rules.WORKFLOWS)."""
        ...


@dataclass
class PassResult:
    looked_at: int = 0
    # Task ids by what the supervisor did, and what it could not do.
    recovered: list[str] = field(default_factory=list)
    # Sent back for a merge, or through the review loop.
    driven: list[str] = field(default_factory=list)
    ended: list[str] = field(default_factory=list)
    canceled: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # How many tasks each owner has.
    owners: dict[str, int] = field(default_factory=dict)


class TemporalWorkflows:
    """Whether a task's RunTask workflow is running, from Temporal."""

    def __init__(self) -> None:
        self._client = None

    async def state(self, task_id: str) -> str:
        from temporalio.client import WorkflowExecutionStatus
        from temporalio.service import RPCError, RPCStatusCode

        from meta_harness.run_service import start

        try:
            if self._client is None:
                self._client = await start.client()
            described = await self._client.get_workflow_handle(task_id).describe()
        except RPCError as err:
            if err.status == RPCStatusCode.NOT_FOUND:
                return "none"
            return "unknown"
        except Exception:  # noqa: BLE001 - Temporal not reachable: the task's workflow is unknown, not gone
            self._client = None
            return "unknown"
        return "running" if described.status in (None, WorkflowExecutionStatus.RUNNING) else "ended"


class Supervisor:
    def __init__(self, tasks: TaskClient, events: Events, workflows: Workflows, host: Any = None) -> None:
        self.tasks = tasks
        self.events = events
        self.workflows = workflows
        self.host = host or code_host
        self._pulls: dict[str, tuple[float, Any]] = {}
        self._mergeable: dict[str, tuple[float, str]] = {}

    async def pass_once(self, now: datetime | None = None) -> PassResult:
        """Look at every open task once. Raises only when the tasks cannot be listed at all."""
        now = now or datetime.now(UTC)
        result = PassResult()
        found = await asyncio.to_thread(cost_facts.every_task, self.tasks, None, list(OPEN_STATUSES))
        before = await asyncio.to_thread(_standings)
        admitted = await asyncio.to_thread(_admitted)
        goals: dict[str, dict | None] = {}
        by_id = {task["id"]: task for task in found}
        for task in found:
            result.looked_at += 1
            try:
                goal = await self._goal(task.get("goal_id"), goals)
                earlier = before.get(task["id"])
                # A wait goes stale once it has lasted WAIT_LONGEST, and stays stale while it is still a wait.
                stale = earlier is not None and (
                    earlier.situation == "merge_wait_stale"
                    or (earlier.situation == "merge_waiting" and now - earlier.since >= WAIT_LONGEST)
                )
                facts, pull = await self._facts(task, goal, task["id"] in admitted, stale, now)
                if task["status"] == "blocked" and facts.goal in SWEPT:
                    how, blocker = await asyncio.to_thread(dependency, task, by_id, before)
                    facts = dataclasses.replace(facts, dependency=how, blocker=blocker)
                decision = decide(facts)
            except Exception as err:  # noqa: BLE001 - one task's facts must not stop the pass for the rest
                result.errors.append(f"{task['id']}: {type(err).__name__}: {err}")
                continue
            result.owners[decision.owner] = result.owners.get(decision.owner, 0) + 1
            changed = await asyncio.to_thread(_save, task["id"], decision)
            if changed:
                self.events.emit(
                    "task_standing",
                    {
                        "owner": decision.owner,
                        "situation": decision.situation,
                        "reason": decision.reason,
                        "ask": decision.ask,
                    },
                    task_id=task["id"],
                    actor="supervisor",
                )
            if decision.action and _due(decision, earlier if not changed else None, now):
                await self._act(task, goal, pull, decision, result)
        await asyncio.to_thread(_forget, [task["id"] for task in found])
        return result

    async def _goal(self, goal_id: str | None, known: dict[str, dict | None]) -> dict | None:
        if not goal_id:
            return None
        if goal_id not in known:
            known[goal_id] = await asyncio.to_thread(_goal, goal_id)
        return known[goal_id]

    async def _facts(
        self, task: dict, goal: dict | None, admitted: bool, stale: bool, now: datetime
    ) -> tuple[Facts, Any]:
        status = task["status"]
        workflow = "none"
        if status in ("running", "taken_over") or (status == "queued" and admitted):
            workflow = await self.workflows.state(task["id"])
        pull_state, pull = "none", None
        if status in ("ready", "review_unresolved", "canceled") and task.get("pr_url"):
            pull_state, pull = await self._pull(task)
        auto_merge = bool(goal and goal.get("auto_merge"))
        how = "unknown"
        # Only a task the supervisor may merge has its pull request's mergeability read.
        if status == "ready" and pull_state == "open" and auto_merge and goal["status"] in SWEPT:
            how = await self._how_mergeable(task, pull)
        return (
            Facts(
                status=status,
                workflow=workflow,
                pull=pull_state,
                goal=goal["status"] if goal else "none",
                waiting_on=task.get("waiting_on") or "",
                cancel_requested=bool(task.get("cancel_requested")),
                admitted=admitted,
                auto_merge=auto_merge,
                mergeable=how,
                # A retry after the worker's own restart is not one of the task's tries, so it always has one.
                drives_left=int(task.get("drives") or 0) < MAX_DRIVES
                or (status == "failed" and failures.restart(task.get("status_reason"))),
                failure=failures.kind(task.get("status_reason"))
                if status in ("failed", "review_unresolved")
                else "permanent",
                retry_due=_since_status(task, now)
                >= RETRY_WAITS[min(int(task.get("drives") or 0), len(RETRY_WAITS) - 1)],
                failed_long_ago=_since_status(task, now) > RETRY_WINDOW,
                # Only a wait can go stale: any other situation is acted on, or is an operator's already.
                stale=stale,
            ),
            pull,
        )

    async def _pull(self, task: dict) -> tuple[str, Any]:
        """The task's pull request's state on the code host, read at most once every PULL_SECONDS."""
        cached = self._pulls.get(task["id"])
        if cached and time.monotonic() - cached[0] < PULL_SECONDS:
            pull = cached[1]
        else:
            try:
                repo, number = code_host.pr_number(task["pr_url"])
            except CodeHostError:
                # A local repo's pull request is a file, with no code host to read it from.
                return "none", None
            try:
                pull = await self.host.read_pull_request(repo, number)
            except Exception:  # noqa: BLE001 - the code host did not answer: unknown, and no one is moved on it
                return "unknown", None
            self._pulls[task["id"]] = (time.monotonic(), pull)
        return ("open" if pull.state in ("open", "draft") else pull.state), pull

    async def _how_mergeable(self, task: dict, pull: Any) -> str:
        """How the task's open pull request stands for a merge (pulls.MERGEABLE), read at most once every
        MERGEABLE_SECONDS. A code host that does not answer reads as unknown, which the supervisor waits on."""
        cached = self._mergeable.get(task["id"])
        if cached and time.monotonic() - cached[0] < MERGEABLE_SECONDS:
            return cached[1]
        try:
            raw = await self.host.pull_request(pull.repo, pull.number)
            head = str((raw.get("head") or {}).get("sha") or pull.head_sha)
            checks = code_host.checks_state(await self.host.check_runs(pull.repo, head)).state if head else "none"
        except Exception:  # noqa: BLE001 - unknown, and the next pass asks again
            return "unknown"
        how = pulls.mergeable(raw, checks)
        self._mergeable[task["id"]] = (time.monotonic(), how)
        return how

    async def _act(self, task: dict, goal: dict | None, pull: Any, decision: Decision, result: PassResult) -> None:
        task_id = task["id"]
        try:
            if decision.action == "recover":
                spent = await asyncio.to_thread(spent_so_far, self.events, task_id)
                recovered = await asyncio.to_thread(self.tasks.recover, task_id, decision.reason, str(spent))
                (result.recovered if recovered["status"] == "queued" else result.ended).append(task_id)
            elif decision.action == "end_at_pull_request":
                await sweep._record_end(goal, task, pull, self.tasks, self.events, sweep.SweepResult(""))
                self._pulls.pop(task_id, None)
                result.ended.append(task_id)
            elif decision.action in ("merge", "review", "retry"):
                spent = await asyncio.to_thread(spent_so_far, self.events, task_id)
                counted = not (decision.action == "retry" and failures.restart(task.get("status_reason")))
                await asyncio.to_thread(
                    self.tasks.drive, task_id, decision.action, decision.reason, str(spent), counted
                )
                self._mergeable.pop(task_id, None)
                self._pulls.pop(task_id, None)
                result.driven.append(task_id)
            elif decision.action == "wait":
                return
            elif decision.action == "cancel_for_goal":
                await asyncio.to_thread(self.tasks.cancel, task_id, decision.reason, "supervisor")
                result.canceled.append(task_id)
            else:
                raise ValueError(f"no action {decision.action!r}")
        except (TaskApiError, CodeHostError, ValueError) as err:
            result.errors.append(f"{task_id}: {decision.action}: {err}")
            await asyncio.to_thread(_acted, task_id, str(err)[:500])
            return
        await asyncio.to_thread(_acted, task_id, "")
        log.warning("task %s: %s, so the supervisor did %s", task_id, decision.reason, decision.action)


def _due(decision: Decision, earlier: standing.Standing | None, now: datetime) -> bool:
    """Whether the supervisor acts on the task this pass. A recovery waits for the task to have stood GRACE
    with no workflow; any action that failed waits longer after each failure before it is tried again."""
    if decision.action == "recover" and (earlier is None or now - earlier.since < GRACE):
        return False
    if earlier is None or earlier.acted_at is None:
        return True
    wait = min(RETRY_FIRST * (2 ** max(earlier.attempts - 1, 0)), RETRY_LONGEST)
    return now - earlier.acted_at >= wait


def dependency(task: dict, by_id: dict[str, dict], before: dict[str, standing.Standing]) -> tuple[str, str]:
    """Where the issues a blocked task waits on stand (rules.DEPENDENCIES), and the first one in the way.

    A blocked task is queued when every issue it depends on closes, which needs each to be worked on. One
    is in the way when its task ended without closing it and waits on an operator (stuck), when it is the
    task's own issue, or when no goal holds it, so no task will ever close it (unheld). An issue whose task
    is running, queued, or being retried by the supervisor is not in the way.
    """
    own = task.get("external_ref") or ""
    with db.connect(autocommit=True) as conn:
        link = goal_store.link(conn, own) if own else None
        refs = (link or {}).get("depends_on") or task.get("depends_on") or []
        unheld = ""
        for ref in refs:
            name = str(ref).removeprefix("github:")
            if ref == own:
                return "stuck", f"{name}, its own issue"
            held = goal_store.link(conn, ref)
            if held is None or held.get("dropped"):
                unheld = unheld or name
                continue
            if held["state"] == "closed" or not held.get("task_id"):
                continue
            other = by_id.get(held["task_id"])
            was = before.get(held["task_id"])
            ended = other and other["status"] in ("failed", "canceled", "review_unresolved")
            if ended and was and was.owner == "operator":
                return "stuck", name
    return ("unheld", unheld) if unheld else ("open", "")


def _since_status(task: dict, now: datetime) -> timedelta:
    """How long the task has been in its status. One with no time kept for it has been there longer than
    anything waits: it changed before the time was kept."""
    changed = task.get("status_changed_at")
    if not changed:
        return timedelta.max
    return now - datetime.fromisoformat(str(changed))


def spent_so_far(events: Events, task_id: str) -> Decimal:
    """What the task's runs have spent since an operator last sent it back to the queue, from the runs'
    attempt_finished events. A recovered task's next workflow counts it against the task's spend limit, so a
    recovery never gives a task its limit to spend again."""
    page = events.read(0, 5000, task_id=task_id, event_type="attempt_finished,task_retried")
    spent = Decimal(0)
    for event in page.get("events") or []:
        if event["event_type"] == "task_retried":
            spent = Decimal(0)
        else:
            spent += Decimal(str((event.get("payload") or {}).get("spent_usd") or 0))
    return spent


def _standings() -> dict[str, standing.Standing]:
    with db.connect(autocommit=True) as conn:
        return standing.read(conn)


def _save(task_id: str, decision: Decision) -> bool:
    with db.connect(autocommit=True) as conn:
        return standing.save(conn, task_id, decision)


def _acted(task_id: str, error: str) -> None:
    with db.connect(autocommit=True) as conn:
        standing.acted(conn, task_id, error)


def _forget(keep: list[str]) -> None:
    with db.connect(autocommit=True) as conn:
        standing.forget(conn, keep)


def _admitted() -> set[str]:
    """The tasks the scheduler holds a place for: it started a workflow for each."""
    with db.connect(autocommit=True) as conn:
        return {row[0] for row in conn.execute("SELECT DISTINCT task_id FROM scheduler.repo_area_lock").fetchall()}


def _goal(goal_id: str) -> dict | None:
    with db.connect(autocommit=True) as conn:
        try:
            return goal_store.get(conn, goal_id)
        except goal_store.NotFound:
            return None
