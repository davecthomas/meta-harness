"""The planner service and its part of the REST API: goals.

    GET, POST  /v1/goals                      list goals, or create one
    GET, PATCH /v1/goals/{id}                 a goal with its issues, or change its title, auto_merge, priority or budget
    GET        /v1/goals/{id}/issues          each issue with its task status, pull request, PR state and checks
    POST       /v1/goals/{id}/pause, /resume, /cancel   resume also starts a goal saved without starting
    POST       /v1/goals/{id}/complete, /reopen  set a goal met by hand, or make a met or canceled goal active again
    POST       /v1/goals/{id}/sweep           take in the goal's labeled issues now
    GET, PUT   /v1/config/{kind}              configuration, with versions and rollback (meta_harness.config.api)
    GET        /health

Operators call it with an operator key (MH_OPERATOR_KEYS); control-plane
services with a service key (MH_SERVICE_KEYS). The planner sweeps every
active goal every MH_PLANNER_SWEEP_SECONDS, and a goal at once when it first
starts (on creation, or on its first resume when it was saved with start false)
or is reopened. A sweep moves a goal to met once its issues are closed and its
tasks are done, and reports the merge or close of a `ready` or
`review_unresolved` task's pull request to the task service.
"""

import asyncio
import contextlib
import logging
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
from meta_harness.planner import drafting, progress, repos, store
from meta_harness.planner.sweep import CodeHost, Events, SweepResult, active_goals, sweep_goal
from meta_harness.planner.tracker import Tracker, parse_ref
from meta_harness.tasks.client import TaskApiError, TaskClient

log = logging.getLogger("meta_harness.planner")

# The most tasks the issues view reads in one Task API page.
TASKS_PAGE = 1000


class GoalIn(BaseModel):
    # The goal's short name, for lists. Left out: the planner's model writes one from the statement.
    title: str | None = Field(default=None, min_length=1, max_length=80)
    statement: str = Field(min_length=1, max_length=4000)
    repos: list[str] = Field(min_length=1)
    # The label that queues an issue for the goal. None: the goal takes only the refs given.
    label: str | None = Field(default=None, min_length=1, max_length=100)
    # Issues the goal holds from the start, by ref (github:owner/repo#n), besides its label's.
    refs: list[str] = Field(default_factory=list, max_length=500)
    modality: str = "fix"
    auto_merge: bool = False
    max_usd: Decimal | None = Field(default=None, gt=0)
    priority: int = 0
    # False: save the goal paused and never started; resume starts it.
    start: bool = True


class RefsIn(BaseModel):
    refs: list[str] = Field(min_length=1, max_length=500)


class DraftIn(BaseModel):
    label: str = Field(min_length=1)
    # The repos to read the label's issues from. Empty: every registered repo.
    repos: list[str] = Field(default_factory=list)
    # The issues to draft from, as refs. Empty: every open issue with the label.
    refs: list[str] = Field(default_factory=list)


class GoalPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    auto_merge: bool | None = None
    priority: int | None = None
    max_usd: Decimal | None = Field(default=None, gt=0)


def add_shipped_configuration(pool, events: Events) -> None:
    """At startup: what the shipped files add to the configuration reaches the active versions."""
    with pool.connection() as conn:
        for saved in [*config_store.add_shipped(conn), *config_store.replace_engines(conn)]:
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
    host: CodeHost | None = None,
    titler=None,
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
            return await sweep_goal(goal, tracker, tasks, events, host)

    name = titler or drafting.title_goal

    async def title_one(goal: dict) -> None:
        """Give a goal with no title one its model writes. A failure leaves it untitled, and the console shows
        the start of its statement instead."""
        try:
            titled = await name(goal["statement"])
        except Exception as err:  # noqa: BLE001 - a title is a convenience: log it and go on
            log.warning("could not title goal %s: %s", goal["id"], err)
            return
        with app.state.pool.connection() as conn:
            if not store.set_title(conn, goal["id"], titled.title):
                return
        events.emit(
            "goal_titled",
            {"goal_id": goal["id"], "title": titled.title, "model": titled.model, "usd_cost": titled.usd_cost},
            actor="planner",
            tags={"goal_id": goal["id"]},
        )

    async def title_untitled() -> None:
        """At startup: title each goal that has none yet, one at a time."""
        with app.state.pool.connection() as conn:
            goals = store.untitled(conn)
        for goal in goals:
            await title_one(goal)

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
        titling = asyncio.create_task(title_untitled()) if interval > 0 else None
        try:
            yield
        finally:
            if loop:
                loop.cancel()
            if titling:
                titling.cancel()
            app.state.pool.close()

    app = FastAPI(title="meta-harness planner", lifespan=lifespan)
    app.state.title_untitled = title_untitled

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

    def move(goal_id: str, who: str, status: str, origins: set[str]) -> dict:
        """Change a goal's status, but only from one of origins: resume is not reopen."""
        goal, _ = move_with(goal_id, who, status, origins)
        return goal

    def move_with(goal_id: str, who: str, status: str, origins: set[str]) -> tuple[dict, dict]:
        """move, returning the fields that changed with their old values, as change_with does."""
        current = load(goal_id)["status"]
        if current not in origins:
            raise HTTPException(status_code=409, detail=f"a {current} goal cannot become {status} this way")
        return change_with(goal_id, who, status=status)

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

    def checked_refs(refs: list[str], goal_repos: list[str]) -> list[tuple[str, str]]:
        """Each ref with its repo's registry name. 422 for a malformed ref or one outside the goal's repos."""
        allowed = {name.lower(): name for name in goal_repos}
        pairs, wrong = [], []
        for ref in dict.fromkeys(refs):
            try:
                repo, number = parse_ref(ref)
            except ValueError:
                wrong.append(f"{ref} is not github:<owner>/<repo>#<number>")
                continue
            if repo.lower() not in allowed:
                wrong.append(f"{ref} is in {repo}, which is not one of the goal's repos")
                continue
            pairs.append((f"github:{allowed[repo.lower()]}#{number}", allowed[repo.lower()]))
        if wrong:
            raise HTTPException(status_code=422, detail="; ".join(wrong))
        return pairs

    def add(goal: dict, who: str, refs: list[str]) -> list[str]:
        """Hold refs for the goal, and record each one held as issue_added. 409 for a ref another goal holds."""
        pairs = checked_refs(refs, goal["repos"])
        try:
            with app.state.pool.connection() as conn:
                added = store.add_refs(conn, goal["id"], pairs)
        except store.GoalError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        for ref in added:
            events.emit("issue_added", {"ref": ref, "goal_id": goal["id"]}, actor=who, tags={"goal_id": goal["id"]})
        return added

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
    async def repo_issues(
        owner: str,
        name: str,
        label: str | None = Query(default=None, min_length=1),
        q: str | None = None,
        cursor: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        who: str = Depends(operator),
    ):
        """A registered repo's open issues, with the label if given and the text q in their titles if given,
        newest first, a page at a time from cursor. Each names the open goal that holds it, if any."""
        [repo] = registered([f"{owner}/{name}"])
        issues = (await open_issues([repo], label))[repo]
        if q:
            issues = [i for i in issues if q.lower() in i.title.lower()]
        issues = sorted(issues, key=lambda i: i.number, reverse=True)
        total = len(issues)
        issues = issues[cursor : cursor + limit]
        with app.state.pool.connection() as conn:
            held = store.holders(conn, [i.ref for i in issues])
        return {
            "repo": repo,
            "label": label,
            "total": total,
            "next_cursor": cursor + limit if cursor + limit < total else None,
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
        if not body.label and not body.refs:
            raise HTTPException(status_code=422, detail="a goal needs a label, or issues picked by ref, or both")
        pairs = checked_refs(body.refs, names)
        fields = body.model_dump(exclude={"refs"})
        try:
            with app.state.pool.connection() as conn:
                goal = store.create(conn, who, store.NewGoal(**{**fields, "repos": names}))
                held = store.add_refs(conn, goal["id"], pairs)
        except store.GoalError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err
        events.emit(
            "goal_created",
            {
                "goal_id": goal["id"],
                "title": goal["title"],
                "statement": goal["statement"],
                "repos": names,
                "label": goal["label"],
                "modality": goal["modality"],
                "auto_merge": goal["auto_merge"],
                "start": body.start,
            },
            actor=who,
        )
        for ref in held:
            events.emit("issue_added", {"ref": ref, "goal_id": goal["id"]}, actor=who, tags={"goal_id": goal["id"]})
        if goal["status"] == "active":
            asyncio.create_task(sweep_one(goal))
        if not goal["title"]:
            asyncio.create_task(title_one(goal))
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

    @app.get("/v1/goals/{goal_id}/issues")
    def goal_issues(goal_id: str, who: str = Depends(operator)) -> dict:
        """Each of the goal's issues with its task's status, pull request URL, pull request state and checks.

        The task's facts come from the Task API and the events recorded on it, never from the task
        service's tables.
        """
        load(goal_id)
        with app.state.pool.connection() as conn:
            links = store.links(conn, goal_id)
        task_ids = [link["task_id"] for link in links if link["task_id"]]
        try:
            found = tasks.list(None, goal_id, TASKS_PAGE) if task_ids else []
            known = {task["id"] for task in found}
            for task_id in task_ids:
                if task_id not in known:
                    found.append(tasks.get(task_id))
        except TaskApiError as err:
            raise HTTPException(status_code=502, detail=f"could not read the goal's tasks: {err}") from err
        try:
            recorded = progress.pull_request_events(events, task_ids) if task_ids else []
        except Exception as err:  # noqa: BLE001 - an unreachable event service is the caller's 502
            raise HTTPException(status_code=502, detail=f"could not read the tasks' events: {err}") from err
        return {"goal_id": goal_id, "issues": progress.issues_progress(links, found, recorded)}

    @app.post("/v1/goals/{goal_id}/issues")
    async def add_issues(goal_id: str, body: RefsIn, who: str = Depends(operator)) -> dict:
        """Hold issues for the goal by ref. An active goal takes them in at once."""
        goal = await asyncio.to_thread(load, goal_id)
        if goal["status"] in ("met", "canceled"):
            raise HTTPException(status_code=409, detail=f"the goal is {goal['status']}: reopen it first")
        added = await asyncio.to_thread(add, goal, who, body.refs)
        if added and goal["status"] == "active":
            asyncio.create_task(sweep_one(goal))
        return {"goal_id": goal_id, "added": added}

    @app.delete("/v1/goals/{goal_id}/issues/{ref:path}")
    def drop_issue(goal_id: str, ref: str, who: str = Depends(operator)) -> dict:
        """Let go of an issue the goal holds and has made no task for."""
        load(goal_id)
        try:
            with app.state.pool.connection() as conn:
                store.drop_ref(conn, goal_id, ref)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail=f"the goal does not hold {ref}") from err
        except store.GoalError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        events.emit("issue_dropped", {"ref": ref, "goal_id": goal_id}, actor=who, tags={"goal_id": goal_id})
        return {"goal_id": goal_id, "dropped": ref}

    @app.patch("/v1/goals/{goal_id}")
    def patch(goal_id: str, body: GoalPatch, who: str = Depends(operator)) -> dict:
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        return change(goal_id, who, **fields)

    @app.post("/v1/goals/{goal_id}/pause")
    def pause(goal_id: str, who: str = Depends(operator)) -> dict:
        return change(goal_id, who, status="paused")

    @app.post("/v1/goals/{goal_id}/resume")
    async def resume(goal_id: str, who: str = Depends(operator)) -> dict:
        """Make the goal active, but not a met or canceled one: that is reopen. Its first start, for a goal
        saved without starting, sweeps it at once."""
        goal, changed = await asyncio.to_thread(
            move_with, goal_id, who, "active", {"active", "paused", "stalled", "budget_exhausted"}
        )
        if "started_at" in changed:
            asyncio.create_task(sweep_one(goal))
        return goal

    @app.post("/v1/goals/{goal_id}/cancel")
    def cancel(goal_id: str, who: str = Depends(operator)) -> dict:
        return change(goal_id, who, status="canceled")

    @app.post("/v1/goals/{goal_id}/complete")
    def complete(goal_id: str, who: str = Depends(operator)) -> dict:
        """Set a goal met by hand, for one whose remaining issues no longer matter."""
        before = load(goal_id)["status"]
        goal = move(goal_id, who, "met", {"active", "paused", "stalled", "met"})
        if before != "met":
            events.emit(
                "goal_met",
                {"goal_id": goal_id, "reason": f"completed by {who}"},
                actor=who,
                tags={"goal_id": goal_id},
            )
        return goal

    @app.post("/v1/goals/{goal_id}/reopen")
    async def reopen(goal_id: str, who: str = Depends(operator)) -> dict:
        """Make a met or canceled goal active again, and take in its label's open issues at once."""
        goal = await asyncio.to_thread(move, goal_id, who, "active", {"met", "canceled", "active"})
        asyncio.create_task(sweep_one(goal))
        return goal

    @app.post("/v1/goals/{goal_id}/sweep")
    async def sweep_now(goal_id: str, who: str = Depends(operator)) -> dict:
        goal = await asyncio.to_thread(load, goal_id)
        if goal["status"] != "active":
            raise HTTPException(status_code=409, detail=f"the goal is {goal['status']}")
        return asdict(await sweep_one(goal))

    return app
