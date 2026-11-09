"""The planner service and its part of the REST API: goals.

    GET, POST  /v1/goals                      list goals, or create one
    GET, PATCH /v1/goals/{id}                 a goal with its issues, or change auto_merge, priority or budget
    POST       /v1/goals/{id}/pause, /resume, /cancel   resume also starts a goal saved without starting
    POST       /v1/goals/{id}/sweep           take in the goal's labeled issues now
    GET, PUT   /v1/config/{kind}              configuration, with versions and rollback (meta_harness.config.api)
    GET        /health

Operators call it with an operator key (MH_OPERATOR_KEYS); control-plane
services with a service key (MH_SERVICE_KEYS). The planner sweeps every
active goal every MH_PLANNER_SWEEP_SECONDS, and a goal at once when it first
starts: on creation, or on its first resume when it was saved with start false.
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
from meta_harness.config import store as config_store
from meta_harness.planner import drafting, repos, store
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
    # False: save the goal paused and never started; resume starts it.
    start: bool = True


class DraftIn(BaseModel):
    label: str = Field(min_length=1)
    # The repos to read the label's issues from. Empty: every registered repo.
    repos: list[str] = Field(default_factory=list)
    # The issues to draft from, as refs. Empty: every open issue with the label.
    refs: list[str] = Field(default_factory=list)


class GoalPatch(BaseModel):
    auto_merge: bool | None = None
    priority: int | None = None
    max_usd: Decimal | None = Field(default=None, gt=0)


def add_shipped_configuration(pool, events: Events) -> None:
    """At startup: what the shipped files add to the configuration reaches the active versions."""
    with pool.connection() as conn:
        for saved in config_store.add_shipped(conn):
            print(f"[planner] configuration {saved['kind']} v{saved['version']}: {saved['note']}", flush=True)
            events.emit(
                "config_changed",
                {
                    "kind": saved["kind"],
                    "version": saved["version"],
                    "origin": saved["origin"],
                    "note": saved["note"],
                    "rolled_back_from": None,
                },
                actor=config_store.SHIPPED_BY,
            )


def create_app(
    events: Events,
    tracker: Tracker,
    tasks: TaskClient,
    database_url: str | None = None,
    operator_keys: dict[str, str] | None = None,
    service_keys: dict[str, str] | None = None,
    sweep_seconds: float | None = None,
    drafter=None,
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
        if config_store.from_database():
            await asyncio.to_thread(add_shipped_configuration, app.state.pool, events)
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
        goal, _ = change_with(goal_id, who, **fields)
        return goal

    def change_with(goal_id: str, who: str, **fields: Any) -> tuple[dict, dict]:
        """Change a goal and record goal_changed. Returns (goal, the fields that changed with their old values)."""
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
        return goal, changed

    drafting.check_settings()
    draft = drafter or drafting.draft_goal

    def registered(names: list[str]) -> list[str]:
        """The registry's names for the repos asked for, or every registered repo. 404 for an unknown one."""
        if not names:
            return [r.name for r in repos.registry().values()]
        unknown = [name for name in names if repos.get(name) is None]
        if unknown:
            raise HTTPException(status_code=404, detail=f"not in the repo registry: {', '.join(unknown)}")
        return [repos.get(name).name for name in names]

    async def open_issues(names: list[str], label: str | None) -> dict[str, list]:
        found = await asyncio.gather(*(tracker.list_open(name, label) for name in names))
        return dict(zip(names, found, strict=True))

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

    @app.get("/v1/labels")
    async def labels(repo: str | None = None, who: str = Depends(operator)) -> dict:
        """Every label on the open issues of the registered repos, or of one, with its issue counts and how many an open goal holds."""
        names = registered([repo] if repo else [])
        issues = await open_issues(names, None)
        with app.state.pool.connection() as conn:
            held = store.holders(conn, [i.ref for found in issues.values() for i in found])
        return {"repos": names, "labels": drafting.count_labels(issues, held)}

    @app.get("/v1/repos/{owner}/{name}/issues")
    async def label_issues(owner: str, name: str, label: str = Query(min_length=1), who: str = Depends(operator)):
        """A registered repo's open issues with the label, each with the open goal that holds it, if any."""
        [repo] = registered([f"{owner}/{name}"])
        issues = (await open_issues([repo], label))[repo]
        with app.state.pool.connection() as conn:
            held = store.holders(conn, [i.ref for i in issues])
        return {
            "repo": repo,
            "label": label,
            "issues": [
                {
                    "ref": i.ref,
                    "number": i.number,
                    "title": i.title,
                    "labels": i.labels,
                    "updated_at": i.updated_at,
                    "held_by": held.get(i.ref),
                }
                for i in issues
            ],
        }

    @app.post("/v1/goals/draft")
    async def draft_goal(body: DraftIn, who: str = Depends(operator)) -> dict:
        """A goal drafted by a model from a label's open issues. Nothing is saved: the operator edits it first."""
        names = registered(body.repos)
        issues = [i for found in (await open_issues(names, body.label)).values() for i in found]
        if body.refs:
            wanted = set(body.refs)
            issues = [i for i in issues if i.ref in wanted]
        try:
            drafted = await draft(body.label, issues)
        except drafting.DraftError as err:
            raise HTTPException(status_code=502, detail=str(err)) from err
        events.emit(
            "goal_drafted",
            {
                "label": body.label,
                "repos": names,
                "issues": len(issues),
                "model": drafted.model,
                "usd_cost": drafted.usd_cost,
            },
            actor=who,
        )
        return {**asdict(drafted), "label": body.label, "repos": names, "refs": [i.ref for i in issues]}

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
                "start": body.start,
            },
            actor=who,
        )
        if goal["status"] == "active":
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
    async def resume(goal_id: str, who: str = Depends(operator)) -> dict:
        """Make the goal active. Its first start, for a goal saved without starting, sweeps it at once."""
        goal, changed = await asyncio.to_thread(change_with, goal_id, who, status="active")
        if "started_at" in changed:
            asyncio.create_task(sweep_one(goal))
        return goal

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
