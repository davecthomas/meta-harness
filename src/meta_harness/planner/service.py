"""The planner service and its part of the REST API: goals.

    GET, POST  /v1/goals                      list goals, or create one
    GET, PATCH /v1/goals/{id}                 a goal with its issues, or change auto_merge, priority or budget
    POST       /v1/goals/{id}/pause, /resume, /cancel
    POST       /v1/goals/{id}/sweep           take in the goal's labeled issues now
    GET, PUT   /v1/config/{kind}              configuration, with versions and rollback (meta_harness.config.api)
    GET        /health

Operators call it with an operator key (MH_OPERATOR_KEYS); control-plane
services with a service key (MH_SERVICE_KEYS). The planner sweeps every
active goal every MH_PLANNER_SWEEP_SECONDS, and a new goal at once.
"""

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from meta_harness import auth, db
from meta_harness.config import api as config_api
from meta_harness.planner import repos, store
from meta_harness.planner.sweep import Events, SweepResult, active_goals, sweep_goal
from meta_harness.planner.tracker import Tracker
from meta_harness.tasks.client import TaskClient


class GoalIn(BaseModel):
    statement: str = Field(min_length=1, max_length=4000)
    repos: list[str] = Field(min_length=1)
    label: str = Field(min_length=1, max_length=100)
    modality: str = "fix"
    auto_merge: bool = False
    max_usd: Decimal | None = Field(default=None, gt=0)
    priority: int = 0


class GoalPatch(BaseModel):
    auto_merge: bool | None = None
    priority: int | None = None
    max_usd: Decimal | None = Field(default=None, gt=0)


def create_app(
    events: Events,
    tracker: Tracker,
    tasks: TaskClient,
    database_url: str | None = None,
    operator_keys: dict[str, str] | None = None,
    service_keys: dict[str, str] | None = None,
    sweep_seconds: float | None = None,
) -> FastAPI:
    operators = operator_keys if operator_keys is not None else auth.keys_from_env("MH_OPERATOR_KEYS")
    services = service_keys if service_keys is not None else auth.keys_from_env("MH_SERVICE_KEYS")
    if not operators and not services:
        raise RuntimeError("set MH_OPERATOR_KEYS or MH_SERVICE_KEYS: the planner refuses to run without keys")
    interval = sweep_seconds if sweep_seconds is not None else float(os.environ.get("MH_PLANNER_SWEEP_SECONDS", "60"))

    # One sweep per goal at a time: the timed sweep, a new goal's first sweep and a requested one never overlap.
    locks: dict[str, asyncio.Lock] = {}

    async def sweep_one(goal: dict) -> SweepResult:
        async with locks.setdefault(goal["id"], asyncio.Lock()):
            return await sweep_goal(goal, tracker, tasks, events)

    async def sweep_forever() -> None:
        while True:
            with contextlib.suppress(Exception):
                for goal in await asyncio.to_thread(active_goals):
                    await sweep_one(goal)
            await asyncio.sleep(interval)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pool = db.pool(database_url)
        loop = asyncio.create_task(sweep_forever()) if interval > 0 else None
        try:
            yield
        finally:
            if loop:
                loop.cancel()
            app.state.pool.close()

    app = FastAPI(title="meta-harness planner", lifespan=lifespan)

    def operator(key: str = Depends(auth.bearer), actor: str | None = Header(default=None, alias="X-MH-Actor")) -> str:
        """Who is acting: the operator key's label, or for a service key (the console) the operator it names."""
        if label := auth.label_for(key, operators):
            return label
        if label := auth.label_for(key, services):
            return actor or label
        raise HTTPException(status_code=401, detail="unknown API key")

    def load(goal_id: str) -> dict:
        try:
            with app.state.pool.connection() as conn:
                return store.get(conn, goal_id)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such goal") from err

    def change(goal_id: str, who: str, **fields: Any) -> dict:
        try:
            with app.state.pool.connection() as conn:
                goal, changed = store.update(conn, goal_id, **fields)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such goal") from err
        except store.GoalError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        if changed:
            events.emit(
                "goal_changed",
                {"goal_id": goal_id, "changes": {k: {"from": str(v), "to": str(goal[k])} for k, v in changed.items()}},
                actor=who,
            )
        return goal

    # The configuration API, for every kind, until the run service has an HTTP API of its own.
    app.include_router(config_api.router(lambda: app.state.pool, operator, events))

    @app.get("/health")
    def health() -> dict[str, str]:
        with app.state.pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/v1/repos")
    def registered_repos(who: str = Depends(operator)) -> dict:
        """The repo registry: the repos a goal may name."""
        return {"repos": [{"name": r.name, "tracker": r.tracker} for r in repos.registry().values()]}

    @app.post("/v1/goals", status_code=201)
    async def create(body: GoalIn, who: str = Depends(operator)) -> dict:
        unknown = [name for name in body.repos if repos.get(name) is None]
        if unknown:
            raise HTTPException(status_code=422, detail=f"not in the repo registry: {', '.join(unknown)}")
        names = [repos.get(name).name for name in body.repos]
        try:
            with app.state.pool.connection() as conn:
                goal = store.create(conn, who, store.NewGoal(**{**body.model_dump(), "repos": names}))
        except store.GoalError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err
        events.emit(
            "goal_created",
            {
                "goal_id": goal["id"],
                "statement": goal["statement"],
                "repos": names,
                "label": goal["label"],
                "modality": goal["modality"],
                "auto_merge": goal["auto_merge"],
            },
            actor=who,
        )
        asyncio.create_task(sweep_one(goal))
        return goal

    @app.get("/v1/goals")
    def list_goals(
        status: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        who: str = Depends(operator),
    ) -> dict:
        with app.state.pool.connection() as conn:
            goals, total = store.page_goals(conn, status.split(",") if status else None, limit, offset)
        return {"goals": goals, "total": total, "offset": offset, "limit": limit}

    @app.get("/v1/goals/{goal_id}")
    def read(goal_id: str, who: str = Depends(operator)) -> dict:
        goal = load(goal_id)
        with app.state.pool.connection() as conn:
            goal["issues"] = store.links(conn, goal_id)
        return goal

    @app.patch("/v1/goals/{goal_id}")
    def patch(goal_id: str, body: GoalPatch, who: str = Depends(operator)) -> dict:
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        return change(goal_id, who, **fields)

    @app.post("/v1/goals/{goal_id}/pause")
    def pause(goal_id: str, who: str = Depends(operator)) -> dict:
        return change(goal_id, who, status="paused")

    @app.post("/v1/goals/{goal_id}/resume")
    def resume(goal_id: str, who: str = Depends(operator)) -> dict:
        return change(goal_id, who, status="active")

    @app.post("/v1/goals/{goal_id}/cancel")
    def cancel(goal_id: str, who: str = Depends(operator)) -> dict:
        return change(goal_id, who, status="canceled")

    @app.post("/v1/goals/{goal_id}/sweep")
    async def sweep_now(goal_id: str, who: str = Depends(operator)) -> dict:
        goal = await asyncio.to_thread(load, goal_id)
        if goal["status"] != "active":
            raise HTTPException(status_code=409, detail=f"the goal is {goal['status']}")
        return asdict(await sweep_one(goal))

    return app
