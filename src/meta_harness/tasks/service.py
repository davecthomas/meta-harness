"""The task service and its Task API.

    POST /v1/tasks                       submit a task, idempotent on the caller's external_ref
    GET  /v1/tasks?external_ref=...      the caller's task with that external_ref
    GET  /v1/tasks?status=queued,...     list tasks; a caller sees its own, a service sees all
    GET  /v1/tasks/{id}                  the task; `held` says why the scheduler holds a queued one
    POST /v1/tasks/{id}/cancel           cancel a task that has not started, or flag a running one
    POST /v1/tasks/{id}/review/claim, /release, /verdict   an operator takes a review over, gives it back, or
                                         gives their verdict
    POST /v1/tasks/{id}/status           a control-plane service moves a task (service keys only)
    GET  /health

Callers authenticate with a Task API key (MH_TASK_API_KEYS). Control-plane
services use a service key (MH_SERVICE_KEYS), which may also create review and
fix tasks and set any task's status. Each change appends an event.
"""

import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from meta_harness import auth, db
from meta_harness.scheduler import holds
from meta_harness.supervisor import standing
from meta_harness.tasks import reviews, spend, store


class Events(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any] | None = None, **fields: Any) -> str: ...

    def read(
        self, from_position: int = 0, limit: int = 500, before_position: int | None = None, **filters: str
    ) -> dict[str, Any]: ...


@dataclass
class Caller:
    label: str
    service: bool
    # Who the audit events name: the key's label, or for a service key (the console) the operator it acts for.
    actor: str = ""


class TaskIn(BaseModel):
    external_ref: str = Field(min_length=1, max_length=300)
    repo: str = Field(min_length=1)
    task_type: str
    priority: int = 0
    title: str = Field(min_length=1, max_length=300)
    description: str
    acceptance_checks: list[str]
    parent_task_id: str | None = None
    goal_id: str | None = None
    depends_on: list[str] | None = None
    paths: list[str] | None = None
    complexity: str | None = None
    max_tier: str | None = None
    max_usd: Decimal | None = Field(default=None, gt=0)
    pin: str | None = None


# A task left with its pull request open and no workflow watching it; an operator clears it.
CLEARABLE = ("ready", "review_unresolved")
# A move to one of these is logged, failed as an error and the rest as warnings, whoever set it.
LOGGED_STATUSES = ("failed", "review_unresolved", "canceled")

log = logging.getLogger("meta_harness.tasks")


class AttemptsIn(BaseModel):
    count: int = Field(ge=1, le=4)


class MessageIn(BaseModel):
    text: str = Field(default="", max_length=8000)
    # next_turn: steer the agent at its next turn. after_stop: when it would otherwise stop. stop: end the run
    # after the turn in progress, keeping its work.
    when: str = "next_turn"


class CancelIn(BaseModel):
    reason: str = "canceled through the Task API"


class StatusIn(BaseModel):
    status: str
    reason: str | None = None
    pr_url: str | None = None
    head_sha: str | None = None
    owner: str | None = None
    # What the running task waits on an operator for: "budget", or "" once it no longer waits.
    waiting_on: str | None = None


class FindingIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    path: str | None = Field(default=None, max_length=500)
    line: int | None = Field(default=None, ge=1)


class VerdictIn(BaseModel):
    verdict: Literal["clean", "changes"]
    # The agent's findings the operator keeps, numbered from 1.
    keep: list[int] = Field(default_factory=list, max_length=200)
    findings: list[FindingIn] = Field(default_factory=list, max_length=100)


class RetryIn(BaseModel):
    # The task's new spend limit, for the runs of its next workflow. Left out: the limit it has.
    max_usd: Decimal | None = Field(default=None, gt=0)


class RecoverIn(BaseModel):
    # What the supervisor found: the task's workflow ended without ending it, or it has none.
    why: str = Field(min_length=1, max_length=500)
    # What the task's runs have spent so far, which its next workflow counts against its spend limit.
    spent_usd: Decimal = Field(default=Decimal(0), ge=0)


class DismissIn(BaseModel):
    # Why the finding is wrong, or not worth a fix run: shown on the review and posted on the pull request.
    reason: str = Field(min_length=1, max_length=2000)


class BudgetIn(BaseModel):
    # raise: carry on, with max_usd as the task's new spend limit when given. end: end the task as it would have.
    action: Literal["raise", "end"]
    max_usd: Decimal | None = Field(default=None, gt=0)


def create_app(
    events: Events,
    database_url: str | None = None,
    caller_keys: dict[str, str] | None = None,
    service_keys: dict[str, str] | None = None,
) -> FastAPI:
    callers = caller_keys if caller_keys is not None else auth.keys_from_env("MH_TASK_API_KEYS")
    services = service_keys if service_keys is not None else auth.keys_from_env("MH_SERVICE_KEYS")
    if not callers and not services:
        raise RuntimeError("set MH_TASK_API_KEYS or MH_SERVICE_KEYS: the Task API refuses to run without keys")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pool = db.pool(database_url)
        try:
            yield
        finally:
            app.state.pool.close()

    app = FastAPI(title="meta-harness task service", lifespan=lifespan)

    def within_ceiling(max_usd: Decimal | None) -> None:
        """Refuse a spend limit above the task policy's ceiling for one task, naming the ceiling."""
        from meta_harness.config import store as config_store

        ceiling = config_store.task_spend_ceiling()
        if max_usd is not None and max_usd > ceiling:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"max_usd {max_usd} is above ${ceiling:.2f}, the most one task may spend "
                    "(the task policy's max_task_usd)"
                ),
            )

    def caller(key: str = Depends(auth.bearer), actor: str | None = Header(default=None, alias="X-MH-Actor")) -> Caller:
        if label := auth.label_for(key, services):
            return Caller(label, service=True, actor=actor or label)
        if label := auth.label_for(key, callers):
            return Caller(label, service=False, actor=label)
        raise HTTPException(status_code=401, detail="unknown API key")

    def service_only(who: Caller = Depends(caller)) -> Caller:
        if not who.service:
            raise HTTPException(status_code=403, detail="only a control-plane service may do this")
        return who

    def visible(task: dict, who: Caller) -> dict:
        if not who.service and task["caller"] != who.label:
            raise HTTPException(status_code=404, detail="no such task")
        return task

    def emit_status(task: dict, previous: str, actor: str) -> None:
        if task["status"] != previous:
            if task["status"] in LOGGED_STATUSES:
                level = logging.ERROR if task["status"] == "failed" else logging.WARNING
                log.log(
                    level, "task %s is %s (was %s): %s", task["id"], task["status"], previous, task["status_reason"]
                )
            events.emit(
                "task_status_changed",
                {"from": previous, "to": task["status"], "reason": task["status_reason"]},
                task_id=task["id"],
                actor=actor,
            )

    @app.get("/health")
    def health() -> dict[str, str]:
        with app.state.pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.post("/v1/tasks", status_code=201)
    def submit(body: TaskIn, response: Response, who: Caller = Depends(caller)) -> dict:
        within_ceiling(body.max_usd)
        try:
            with app.state.pool.connection() as conn:
                task, created = store.create(conn, who.label, store.NewTask(**body.model_dump()), internal=who.service)
        except store.TaskError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err
        if created:
            events.emit(
                "task_created",
                {
                    "external_ref": task["external_ref"],
                    "repo": task["repo"],
                    "task_type": task["task_type"],
                    "status": task["status"],
                    "goal_id": task["goal_id"],
                    "parent_task_id": task["parent_task_id"],
                },
                task_id=task["id"],
                actor=who.actor,
            )
        else:
            response.status_code = 200
        return task

    @app.get("/v1/tasks")
    def find_or_list(
        external_ref: str | None = None,
        status: str | None = None,
        goal_id: str | None = None,
        pr: bool | None = None,
        q: str | None = Query(default=None, max_length=200),
        waiting: bool | None = None,
        smoke: bool = False,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        who: Caller = Depends(caller),
    ) -> dict:
        owner = None if who.service else who.label
        with app.state.pool.connection() as conn:
            if external_ref is not None:
                try:
                    return store.find(conn, owner, external_ref)
                except store.NotFound as err:
                    raise HTTPException(status_code=404, detail="no such task") from err
            statuses = status.split(",") if status else None
            tasks, total = store.list_tasks(conn, owner, statuses, goal_id, limit, offset, pr, q, waiting, smoke)
            held = holds.for_tasks(conn, [t["id"] for t in tasks if t["status"] == "queued"])
            standings = standing.for_tasks(conn, [t["id"] for t in tasks if t["status"] != "closed"])
            for task in tasks:
                task["held"] = held.get(task["id"])
                task["standing"] = standings.get(task["id"])
            return {"tasks": tasks, "total": total, "offset": offset, "limit": limit}

    @app.get("/v1/tasks/{task_id}")
    def read(task_id: str, who: Caller = Depends(caller)) -> dict:
        try:
            with app.state.pool.connection() as conn:
                task = visible(store.get(conn, task_id), who)
                # Why the scheduler holds a queued task, from the scheduler's own record of it.
                task["held"] = holds.get(conn, task_id) if task["status"] == "queued" else None
                # Who has the task, from the supervisor's own record of it.
                task["standing"] = standing.get(conn, task_id) if task["status"] != "closed" else None
                return task
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such task") from err

    @app.post("/v1/tasks/{task_id}/cancel")
    def cancel(task_id: str, body: CancelIn | None = None, who: Caller = Depends(caller)) -> dict:
        reason = (body or CancelIn()).reason
        try:
            with app.state.pool.connection() as conn:
                visible(store.get(conn, task_id), who)
                task, previous = store.request_cancel(conn, task_id, reason)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such task") from err
        except store.TaskError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        if task["status"] == "canceled":
            emit_status(task, previous, who.actor)
        else:
            events.emit("task_cancel_requested", {"reason": reason}, task_id=task_id, actor=who.actor)
        return task

    @app.post("/v1/tasks/{task_id}/attempts")
    def attempts(task_id: str, body: AttemptsIn, who: Caller = Depends(caller)) -> dict:
        """Run a task that has not started as several attempts at once, on its tier's candidates, for the operator
        to compare and pick one."""
        try:
            with app.state.pool.connection() as conn:
                visible(store.get(conn, task_id), who)
                return store.set_attempts(conn, task_id, body.count)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such task") from err
        except store.TaskError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err

    @app.post("/v1/tasks/{task_id}/messages", status_code=202)
    def message(task_id: str, body: MessageIn, who: Caller = Depends(caller)) -> dict:
        """An operator's message to a running task, or stop after the current turn. The scheduler hands it to the
        task's RunTask workflow, which gives it to the run in progress."""
        if body.when == "budget":
            raise HTTPException(status_code=422, detail="answer a budget hold with POST /v1/tasks/{id}/budget")
        if body.when == "review":
            raise HTTPException(status_code=422, detail="answer a held review with POST /v1/tasks/{id}/reviews/...")
        if body.when != "stop" and not body.text.strip():
            raise HTTPException(status_code=422, detail="a message needs text")
        if body.when == "choose" and not body.text.strip().isdigit():
            raise HTTPException(status_code=422, detail="choose names the attempt's number")
        try:
            with app.state.pool.connection() as conn:
                visible(store.get(conn, task_id), who)
                queued = store.add_message(conn, task_id, body.text.strip(), body.when, who.actor)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such task") from err
        except store.TaskError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        if queued["deliver"] == "choose":
            events.emit(
                "attempt_chosen",
                {"message_id": queued["id"], "attempt": int(queued["body"])},
                task_id=task_id,
                actor=who.actor,
            )
        else:
            events.emit(
                "operator_message",
                {"message_id": queued["id"], "text": queued["body"], "when": queued["deliver"]},
                task_id=task_id,
                actor=who.actor,
            )
        return {"message_id": queued["id"], "when": queued["deliver"]}

    def requeue(task_id: str, action: str, max_usd: Decimal | None, who: Caller) -> dict:
        try:
            with app.state.pool.connection() as conn:
                visible(store.get(conn, task_id), who)
                task, previous = store.requeue(conn, task_id, action, max_usd, who.actor)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such task") from err
        except store.TaskError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        emit_status(task, previous, who.actor)
        retried = {"action": action, "resume": task["resume"], "max_usd": task["max_usd"], "retries": task["retries"]}
        events.emit("task_retried", retried, task_id=task_id, actor=who.actor)
        return task

    @app.post("/v1/tasks/{task_id}/retry", status_code=202)
    def retry(task_id: str, body: RetryIn | None = None, who: Caller = Depends(caller)) -> dict:
        """Send a review_unresolved, failed or canceled task back to the queue. With a pull request it resumes
        at the review loop (or closes, when the pull request has merged); without one it starts a new first run.
        Its runs start from nothing spent, against max_usd when given."""
        within_ceiling((body or RetryIn()).max_usd)
        return requeue(task_id, "retry", (body or RetryIn()).max_usd, who)

    @app.post("/v1/tasks/{task_id}/merge", status_code=202)
    def merge(task_id: str, who: Caller = Depends(caller)) -> dict:
        """Ask for a ready or review_unresolved task's pull request to be merged: through the merge queue, as an
        auto-merge would, whatever its goal's auto_merge says, then the wait for the merge commit's checks."""
        return requeue(task_id, "merge", None, who)

    def review_word(task_id: str, word: str, who: Caller) -> dict:
        try:
            with app.state.pool.connection() as conn:
                visible(store.get(conn, task_id), who)
                return store.review_word(conn, task_id, word, who.actor)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such task") from err
        except store.TaskError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err

    @app.post("/v1/tasks/{task_id}/reviews/{round_no}/findings/{finding}/dismiss", status_code=202)
    def dismiss_finding(
        task_id: str, round_no: int, finding: int, body: DismissIn, who: Caller = Depends(caller)
    ) -> dict:
        """Dismiss one finding (numbered from 1) of the review a task holds for an operator, with why: the fix
        run is not asked for it. With every finding dismissed, the round counts as clean."""
        if finding < 1:
            raise HTTPException(status_code=422, detail="findings are numbered from 1")
        reason = " ".join(body.reason.split())
        task = review_word(task_id, f"dismiss:{round_no}:{finding}:{reason}", who)
        dismissed = {"round": round_no, "finding": finding, "reason": reason}
        events.emit("finding_dismissed", dismissed, task_id=task_id, actor=who.actor)
        return task

    def operator_review(task_id: str, who: Caller, change, event: str, payload: dict) -> dict:
        try:
            with app.state.pool.connection() as conn:
                visible(store.get(conn, task_id), who)
                task = change(conn)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such task") from err
        except store.TaskError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        events.emit(event, payload, task_id=task_id, actor=who.actor)
        return task

    @app.post("/v1/tasks/{task_id}/review/claim", status_code=202)
    def claim_review(task_id: str, who: Caller = Depends(caller)) -> dict:
        """Take the review of a running task's pull request over. The agent's review still finishes, and the
        round then waits for this operator's verdict. The first claim wins; another operator's is refused,
        naming who holds it."""
        return operator_review(
            task_id, who, lambda conn: store.claim_review(conn, task_id, who.actor), "operator_review_claimed", {}
        )

    @app.post("/v1/tasks/{task_id}/review/release", status_code=202)
    def release_review(task_id: str, who: Caller = Depends(caller)) -> dict:
        """Give a claimed review back: the round decides on the agent's verdict."""
        return operator_review(
            task_id,
            who,
            lambda conn: store.end_review_claim(conn, task_id, who.actor, "release"),
            "operator_review_released",
            {},
        )

    @app.post("/v1/tasks/{task_id}/review/verdict", status_code=202)
    def review_verdict(task_id: str, body: VerdictIn, who: Caller = Depends(caller)) -> dict:
        """The verdict of the operator who holds the review, once the agent's review has finished: which of the
        agent's findings they keep (numbered from 1) and their own. With none of either the round is clean;
        otherwise the fix run is asked for them. Posted on the pull request under the operator's name."""
        if body.verdict == "clean" and (body.keep or body.findings):
            raise HTTPException(status_code=422, detail="a clean verdict keeps no finding and adds none")
        if body.verdict == "changes" and not (body.keep or body.findings):
            raise HTTPException(status_code=422, detail="a changes verdict keeps or adds at least one finding")
        word = "verdict:" + json.dumps(
            {"keep": sorted(set(body.keep)), "findings": [f.model_dump(exclude_none=True) for f in body.findings]}
        )
        submitted = {"verdict": body.verdict, "kept": len(set(body.keep)), "findings": len(body.findings)}
        return operator_review(
            task_id,
            who,
            lambda conn: store.end_review_claim(conn, task_id, who.actor, word),
            "operator_review_submitted",
            submitted,
        )

    @app.post("/v1/tasks/{task_id}/reviews/{round_no}/proceed", status_code=202)
    def proceed_review(task_id: str, round_no: int, who: Caller = Depends(caller)) -> dict:
        """End the wait on a held review now: its fix run starts with the findings not dismissed, or, with
        none left, the round counts as clean."""
        task = review_word(task_id, f"proceed:{round_no}", who)
        events.emit("review_proceeded", {"round": round_no}, task_id=task_id, actor=who.actor)
        return task

    @app.post("/v1/tasks/{task_id}/budget", status_code=202)
    def budget(task_id: str, body: BudgetIn, who: Caller = Depends(caller)) -> dict:
        """An operator's answer to a task held for budget: raise its spend limit (or carry on, when its goal's
        budget was raised) or end it. The scheduler hands the answer to the task's workflow."""
        within_ceiling(body.max_usd)
        try:
            with app.state.pool.connection() as conn:
                visible(store.get(conn, task_id), who)
                task = store.decide_budget(conn, task_id, body.action, body.max_usd, who.actor)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such task") from err
        except store.TaskError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        decided = {"action": body.action, "max_usd": str(body.max_usd) if body.max_usd is not None else None}
        events.emit("budget_decision", decided, task_id=task_id, actor=who.actor)
        return task

    @app.get("/v1/messages/pending")
    def pending(who: Caller = Depends(service_only)) -> dict:
        """Operator messages the scheduler has yet to hand over. Services only."""
        with app.state.pool.connection() as conn:
            return {"messages": store.pending_messages(conn)}

    @app.post("/v1/messages/{message_id}/delivered")
    def delivered(message_id: int, who: Caller = Depends(service_only)) -> dict:
        with app.state.pool.connection() as conn:
            store.mark_delivered(conn, message_id)
        return {"message_id": message_id}

    @app.get("/v1/tasks/{task_id}/reviews")
    def task_reviews(task_id: str, who: Caller = Depends(caller)) -> dict:
        """The task's review rounds on its pull request, oldest first, each with what came next."""
        try:
            with app.state.pool.connection() as conn:
                task = visible(store.get(conn, task_id), who)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such task") from err
        try:
            recorded = events.read(0, 5000, task_id=task_id, event_type=",".join(reviews.REVIEW_EVENT_TYPES))["events"]
        except Exception as err:  # noqa: BLE001 - the event service is the caller's 502
            raise HTTPException(status_code=502, detail=f"could not read the task's events: {err}") from err
        return {"task_id": task_id, "rounds": reviews.rounds(task, recorded)}

    @app.get("/v1/tasks/{task_id}/spend")
    def task_spend(task_id: str, who: Caller = Depends(caller)) -> dict:
        """Where the task's money went: each of its runs with its kind, turns, tokens and cost, oldest first,
        and their totals against the task's spend limit."""
        try:
            with app.state.pool.connection() as conn:
                task = visible(store.get(conn, task_id), who)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such task") from err
        try:
            recorded = events.read(0, 5000, task_id=task_id, event_type=",".join(spend.SPEND_EVENT_TYPES))["events"]
        except Exception as err:  # noqa: BLE001 - the event service is the caller's 502
            raise HTTPException(status_code=502, detail=f"could not read the task's events: {err}") from err
        rows = spend.runs(recorded)
        return {"task_id": task_id, "max_usd": task["max_usd"], "runs": rows, "totals": spend.totals(rows)}

    @app.post("/v1/tasks/{task_id}/clear")
    def clear(task_id: str, who: Caller = Depends(caller)) -> dict:
        """Take a ready or review_unresolved task off the list: closed when its pull request merged, as the
        merge's recorded event shows, else canceled. The pull request on GitHub is left as it is."""
        try:
            with app.state.pool.connection() as conn:
                task = visible(store.get(conn, task_id), who)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such task") from err
        if task["status"] not in CLEARABLE:
            raise HTTPException(status_code=409, detail=f"a {task['status']} task is not waiting to be cleared")
        number = (task.get("pr_url") or "").rstrip("/").rsplit("/", 1)[-1] or "?"
        try:
            merged = bool(events.read(0, 1, task_id=task_id, event_type="pull_request_merged")["events"])
        except Exception:  # noqa: BLE001 - an unreadable record clears as canceled, which the reason says
            merged = False
        if merged:
            status, reason = "closed", f"its pull request #{number} merged"
        else:
            status, reason = "canceled", f"cleared by {who.actor} with pull request #{number} not merged"
        with app.state.pool.connection() as conn:
            task, previous = store.set_status(conn, task_id, status, reason)
        emit_status(task, previous, who.actor)
        return task

    @app.post("/v1/tasks/{task_id}/recover")
    def recover(task_id: str, body: RecoverIn, who: Caller = Depends(service_only)) -> dict:
        """The supervisor's move for a running or admitted task that has no RunTask workflow behind it: back
        to the queue for a new workflow, canceled when it was asked to cancel, or failed once it has been
        recovered too often. 409 for a task in any other status."""
        try:
            with app.state.pool.connection() as conn:
                task, previous = store.recover(conn, task_id, body.why, body.spent_usd)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such task") from err
        except store.TaskError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        emit_status(task, previous, who.actor)
        events.emit(
            "task_recovered",
            {"from": previous, "to": task["status"], "recoveries": task["recoveries"], "why": body.why},
            task_id=task_id,
            actor=who.actor,
        )
        return task

    @app.post("/v1/tasks/{task_id}/status")
    def set_status(task_id: str, body: StatusIn, who: Caller = Depends(service_only)) -> dict:
        settable = {"pr_url", "head_sha", "owner", "waiting_on"}
        fields = {k: v for k, v in body.model_dump().items() if k in settable and v is not None}
        try:
            with app.state.pool.connection() as conn:
                before = store.get(conn, task_id)
                task, previous = store.set_status(conn, task_id, body.status, body.reason, **fields)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such task") from err
        except store.TaskError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        emit_status(task, previous, who.actor)
        if task["waiting_on"] == "budget" and before.get("waiting_on") != "budget":
            log.warning("task %s is held for budget: %s", task_id, task["status_reason"])
            events.emit("budget_hold", {"reason": task["status_reason"] or ""}, task_id=task_id, actor=who.actor)
        # A review held before its fix run, or a round waiting for the verdict of the operator who took it over.
        if task["waiting_on"] in ("review", "operator_review") and before.get("waiting_on") != task["waiting_on"]:
            events.emit("review_hold", {"reason": task["status_reason"] or ""}, task_id=task_id, actor=who.actor)
        return task

    return app
