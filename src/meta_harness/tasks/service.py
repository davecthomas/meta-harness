"""The task service and its Task API.

    POST /v1/tasks                       submit a task, idempotent on the caller's external_ref
    GET  /v1/tasks?external_ref=...      the caller's task with that external_ref
    GET  /v1/tasks?status=queued,...     list tasks; a caller sees its own, a service sees all
    GET  /v1/tasks/{id}
    POST /v1/tasks/{id}/cancel           cancel a task that has not started, or flag a running one
    POST /v1/tasks/{id}/status           a control-plane service moves a task (service keys only)
    GET  /health

Callers authenticate with a Task API key (MH_TASK_API_KEYS). Control-plane
services use a service key (MH_SERVICE_KEYS), which may also create review and
fix tasks and set any task's status. Each change appends an event.
"""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from meta_harness import auth, db
from meta_harness.tasks import reviews, store


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
            tasks, total = store.list_tasks(conn, owner, statuses, goal_id, limit, offset, pr, q, waiting)
            return {"tasks": tasks, "total": total, "offset": offset, "limit": limit}

    @app.get("/v1/tasks/{task_id}")
    def read(task_id: str, who: Caller = Depends(caller)) -> dict:
        try:
            with app.state.pool.connection() as conn:
                return visible(store.get(conn, task_id), who)
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

    @app.post("/v1/tasks/{task_id}/budget", status_code=202)
    def budget(task_id: str, body: BudgetIn, who: Caller = Depends(caller)) -> dict:
        """An operator's answer to a task held for budget: raise its spend limit (or carry on, when its goal's
        budget was raised) or end it. The scheduler hands the answer to the task's workflow."""
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
        return task

    return app
