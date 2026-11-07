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

from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field

from meta_harness import auth, db
from meta_harness.tasks import store


class Events(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any] | None = None, **fields: Any) -> str: ...


@dataclass
class Caller:
    label: str
    service: bool


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


class CancelIn(BaseModel):
    reason: str = "canceled through the Task API"


class StatusIn(BaseModel):
    status: str
    reason: str | None = None
    pr_url: str | None = None
    head_sha: str | None = None
    owner: str | None = None


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

    def caller(key: str = Depends(auth.bearer)) -> Caller:
        if label := auth.label_for(key, services):
            return Caller(label, service=True)
        if label := auth.label_for(key, callers):
            return Caller(label, service=False)
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
                actor=who.label,
            )
        else:
            response.status_code = 200
        return task

    @app.get("/v1/tasks")
    def find_or_list(
        external_ref: str | None = None,
        status: str | None = None,
        goal_id: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
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
            return {"tasks": store.list_tasks(conn, owner, statuses, goal_id, limit)}

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
            emit_status(task, previous, who.label)
        else:
            events.emit("task_cancel_requested", {"reason": reason}, task_id=task_id, actor=who.label)
        return task

    @app.post("/v1/tasks/{task_id}/status")
    def set_status(task_id: str, body: StatusIn, who: Caller = Depends(service_only)) -> dict:
        fields = {k: v for k, v in body.model_dump().items() if k in {"pr_url", "head_sha", "owner"} and v is not None}
        try:
            with app.state.pool.connection() as conn:
                task, previous = store.set_status(conn, task_id, body.status, body.reason, **fields)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such task") from err
        except store.TaskError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        emit_status(task, previous, who.label)
        return task

    return app
