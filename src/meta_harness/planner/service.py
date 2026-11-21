"""The planner service and its part of the REST API: goals.

    GET, POST  /v1/goals                      list goals, or create one
    GET, PATCH /v1/goals/{id}                 a goal with its issues, or change any field an operator set on it
    GET        /v1/goals/{id}/issues          each issue with its task status, pull request, PR state and checks
    POST       /v1/goals/{id}/pause, /resume, /cancel   resume also starts a goal saved without starting
                                              cancel also ends the goal's tasks that have not started
    POST       /v1/goals/{id}/resume-budget   raise the budget of a goal over it or stopped by it (budget_api.py)
    POST       /v1/goals/{id}/complete, /reopen  set a goal met by hand, or make a met or canceled goal active again
    POST       /v1/goals/{id}/sweep           take in the goal's labeled issues now
    POST       /v1/goals/{id}/issues/run-anyway, /split   answer an issue the sweep held (sizing_api.py)
    GET, PUT   /v1/config/{kind}              configuration, with versions and rollback (meta_harness.config.api)
    GET, POST  /v1/admin/users                the console's users: invite, set a role, deprovision (users.py)
    GET        /v1/costs                      costs over time, by source, goal, model and more (costs/api.py)
    GET, PUT   /v1/me/settings                the signed-in user's own settings
    GET        /v1/activity                   every pull request opened lately, by state, for the Overview's row
    GET        /health

Operators call it with an operator key (MH_OPERATOR_KEYS); control-plane
services with a service key (MH_SERVICE_KEYS). The planner sweeps every
active goal, and every goal stopped by its budget, every MH_PLANNER_SWEEP_SECONDS, and a goal at once when it first
starts (on creation, or on its first resume when it was saved with start false)
or is reopened. A sweep moves a goal to met once its issues are closed and its
tasks are done, and reports the merge or close of a `ready` or
`review_unresolved` task's pull request to the task service. A goal no sweep
covers, met, canceled, paused or stalled, is followed up hourly instead: its
issues' labels refreshed and its canceled tasks' pull requests watched for
their end (sweep.follow_up_goal), and so are the canceled tasks of no goal
(sweep.follow_up_goalless).
"""

import asyncio
import logging
import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from meta_harness import outbound
from meta_harness import charter
from meta_harness.outbound import rules as writing_rules
from meta_harness import auth, db
from meta_harness.costs import api as costs_api
from meta_harness.config import api as config_api
from meta_harness.config import store as config_store
from meta_harness.config.kinds import ConfigError, sandbox_ceiling
from meta_harness.github_app import GitHubApps
from meta_harness.run_service import code_host
from meta_harness.run_service.provenance import COMMENT, Provenance, payload
from meta_harness.planner import (
    activity,
    auto_split,
    follow_ups,
    budget_api,
    cost_facts,
    drafting,
    goal_costs,
    merge_checks,
    naming,
    plan_api,
    planning,
    plans,
    progress,
    repos,
    settings,
    sizing,
    suggestions,
    updates,
    sizing_api,
    store,
    users,
)
from meta_harness.planner.sweep import (
    FOLLOW_UP_SECONDS,
    CodeHost,
    Events,
    SweepResult,
    cancel_goal_tasks,
    follow_up_goal,
    follow_up_goalless,
    followed_up_goals,
    refresh_states,
    sweep_goal,
    swept_goals,
    goal_tracker,
)
from meta_harness.planner.tracker import Tracker, TrackerError, parse_ref
from meta_harness.trackers import registry as tracker_registry
from meta_harness.trackers.base import Source
from meta_harness.supervisor import ledger
from meta_harness.supervisor.goals import goal_standing
from meta_harness.supervisor.loop import Supervisor, TemporalWorkflows, Workflows
from meta_harness.supervisor.rules import OPEN_STATUSES
from meta_harness.tasks.client import TaskApiError, TaskClient

log = logging.getLogger("meta_harness.planner")

# The most tasks the issues view reads in one Task API page.
TASKS_PAGE = 1000
# The goals a timed sweep covers, whose record of their issues' states it keeps current.
SWEPT_STATUSES = ("active", "budget_exhausted")
# How long a read of the tracker for a goal no sweep covers stands, before its page reads it again.
STATE_CHECK_SECONDS = 60.0
# How long a repo's usual check times are kept before they are read again.
USUAL_CHECKS_SECONDS = 600.0


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
    # How much is done without asking: ask, standard or full (store.AUTONOMY). Left out: full when auto_merge
    # is on, else standard.
    autonomy: Literal["ask", "standard", "full"] | None = None
    max_usd: Decimal | None = Field(default=None, gt=0)
    # What the budget does when it is reached, and how far past it a block goal may go, in percent (budget.py).
    budget_mode: Literal["block", "warn"] = "block"
    budget_overage_percent: int = Field(default=10, ge=0, le=1000)
    # The most of its tasks that run at once, no more than the system's. None: the system's.
    max_parallel: int | None = Field(default=None, ge=1)
    priority: int = 0
    # True: an issue missing acceptance checks, paths or a testable outcome is held, not run (sizing.py).
    strict_issues: bool = False
    # Whether the files its runs create take a provenance header. None: config/provenance.json decides.
    provenance: bool | None = None
    # The issue tracker the goal uses, one for all its issues. Left out: the default tracker.
    tracker: str | None = Field(default=None, min_length=1, max_length=40)
    # What the tracker asks of a goal besides its repos (GET /v1/trackers, goal_settings): for Jira, its project.
    tracker_settings: dict[str, str] = Field(default_factory=dict)
    # False: save the goal paused and never started; resume starts it.
    start: bool = True


class LineCommentIn(BaseModel):
    path: str = Field(min_length=1)
    line: int = Field(ge=1)
    start_line: int | None = Field(default=None, ge=1)
    body: str = Field(min_length=1, max_length=8000)


class RefsIn(BaseModel):
    refs: list[str] = Field(min_length=1, max_length=500)


class DraftIn(BaseModel):
    # The label to draft from, or several in `labels`: the draft reads the open issues of every one.
    label: str | None = Field(default=None, min_length=1)
    labels: list[str] = Field(default_factory=list, max_length=20)
    # The repos to read the label's issues from. Empty: every registered repo.
    repos: list[str] = Field(default_factory=list)
    # The issues to draft from, as refs. Empty: every open issue with the label.
    refs: list[str] = Field(default_factory=list)
    # Which tracker's issues, and what that tracker asks of a goal. Left out: the default tracker's.
    tracker: str | None = Field(default=None, min_length=1, max_length=40)
    tracker_settings: dict[str, str] = Field(default_factory=dict)


class GoalPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    # The goal's description. Its tasks already made keep the issues they came from; the next planning reads it.
    statement: str | None = Field(default=None, min_length=1, max_length=4000)
    # The label and repos the sweep lists issues by. Tasks the goal already made keep their issues; the next
    # sweep takes in issues by the new ones; an issue with no task yet in a repo dropped from repos is let go.
    # A label sent as null: the goal takes only the issues an operator picked for it by ref.
    label: str | None = Field(default=None, min_length=1, max_length=100)
    repos: list[str] | None = Field(default=None, min_length=1)
    auto_merge: bool | None = None
    # How much is done without asking: ask, standard or full (store.AUTONOMY). full turns auto_merge on.
    autonomy: Literal["ask", "standard", "full"] | None = None
    priority: int | None = None
    # Sent as null: the goal has no budget.
    max_usd: Decimal | None = Field(default=None, gt=0)
    budget_mode: Literal["block", "warn"] | None = None
    budget_overage_percent: int | None = Field(default=None, ge=0, le=1000)
    # Sent as null: the goal goes back to the system's limit.
    max_parallel: int | None = Field(default=None, ge=1)
    # An issue it has not made a task yet is held, or let go, at the next sweep.
    strict_issues: bool | None = None
    # Whether the files its runs create from now on take a provenance header. Sent as null:
    # config/provenance.json decides again.
    provenance: bool | None = None


def system_max_parallel() -> int | None:
    """The task policy's max_parallel, the most tasks that run at once; None when the policy cannot be read,
    so a goal's own limit is not checked against it."""
    try:
        return config_store.max_parallel()
    except Exception:  # noqa: BLE001 - the scheduler refuses a bad policy at its own startup
        return None


def parallel_reason(max_parallel: int | None) -> str | None:
    """Why a goal cannot take this parallel limit: one above the system's."""
    system = system_max_parallel()
    if max_parallel is not None and system is not None and max_parallel > system:
        return f"max_parallel {max_parallel} is more than the system's {system}; a goal may only lower it"
    return None


def add_shipped_configuration(pool, events: Events) -> None:
    """At startup: what the shipped files add to the configuration reaches the active versions."""
    with pool.connection() as conn:
        for saved in [
            *config_store.sync_locked(conn),
            *config_store.add_shipped(conn),
            *config_store.add_shipped_to_profiles(conn),
            *config_store.replace_engines(conn),
        ]:
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


def supervisor_seconds() -> float:
    """How often the supervisor looks at every open task, from MH_SUPERVISOR_SECONDS: 60 when it is not set,
    0 to turn the supervisor off. Raises RuntimeError, naming the setting, for anything else."""
    raw = os.environ.get("MH_SUPERVISOR_SECONDS", "60")
    try:
        seconds = float(raw)
    except ValueError as err:
        raise RuntimeError(f"MH_SUPERVISOR_SECONDS must be a number of seconds, 0 or more, not {raw!r}") from err
    if seconds < 0 or seconds != seconds:
        raise RuntimeError(f"MH_SUPERVISOR_SECONDS must be a number of seconds, 0 or more, not {raw!r}")
    return seconds


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
    workflows: Workflows | None = None,
    apps: GitHubApps | None = None,
    sizer: sizing.Sizer | None = None,
    update_writer: updates.Writer | None = None,
    fix_assessor: suggestions.Assessor | None = None,
    namer: naming.Namer | None = None,
) -> FastAPI:
    operators = operator_keys if operator_keys is not None else auth.keys_from_env("MH_OPERATOR_KEYS")
    services = service_keys if service_keys is not None else auth.keys_from_env("MH_SERVICE_KEYS")
    if not operators and not services:
        raise RuntimeError("set MH_OPERATOR_KEYS or MH_SERVICE_KEYS: the planner refuses to run without keys")
    interval = sweep_seconds if sweep_seconds is not None else float(os.environ.get("MH_PLANNER_SWEEP_SECONDS", "60"))

    # One sweep per goal at a time: the timed sweep, a new goal's first sweep and a requested one never overlap.
    locks: dict[str, asyncio.Lock] = {}
    # Each issue is sized before it becomes a task: one model call, the first time a sweep sees it.
    size = sizer or sizing.size_issue

    async def sweep_one(goal: dict) -> SweepResult:
        async with locks.setdefault(goal["id"], asyncio.Lock()):
            result = await sweep_goal(goal, tracker, tasks, events, host, size)
            # A merged pull request's follow-up findings become one issue, for a later goal to take in.
            try:
                result.follow_ups = await follow_ups.file(goal, tracker, events, result.errors)
            except Exception as err:  # noqa: BLE001 - logged; the next sweep looks again
                log.error("goal %s: its follow-ups could not be filed: %s: %s", goal["id"], type(err).__name__, err)
            # An issue the sweep held for its size is split by the planner itself, within its tries; the next
            # sweep takes the parts in.
            try:
                result.split = await auto_split.run(goal, tracker, events, tasks)
            except Exception as err:  # noqa: BLE001 - logged; the held issue keeps its Split and Run anyway
                log.error(
                    "goal %s: the planner could not split its held issues: %s: %s", goal["id"], type(err).__name__, err
                )
            return result

    name = titler or drafting.title_goal
    # The GitHub Apps, to list the repos they are installed on: the tracker's, else the environment's.
    github = tracker.adapter("github") if hasattr(tracker, "adapter") else tracker
    apps = apps or getattr(github, "apps", None) or GitHubApps()

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

    async def issues_to_plan(goal: dict) -> list:
        """The issues a goal already has, for its planning task to plan over: its label's open issues, those
        assigned to the meta-harness when it asked for them, and those picked for it by ref. Each once, and none
        that another open goal holds. Empty for a goal that plans its issues from its description.

        Raises:
            TrackerError: The goal's tracker could not be read.
        """
        source = goal_tracker(tracker, goal)
        found: dict[str, Any] = {}
        for repo in goal["repos"]:
            listed = await source.list_open(repo, goal["label"]) if goal["label"] else []
            for issue in [*listed, *await source.list_assigned(repo)]:
                found.setdefault(issue.ref, issue)

        def held() -> tuple[list[dict], dict[str, str], set[str]]:
            with app.state.pool.connection() as conn:
                return (
                    store.links(conn, goal["id"]),
                    store.holders(conn, list(found)),
                    store.dropped_refs(conn, goal["id"]),
                )

        links, holders, removed = await asyncio.to_thread(held)
        for link in links:
            if link["ref"] not in found and link["state"] == "open":
                found[link["ref"]] = await source.read(link["ref"])
        # One whose task is already under way is not the plan's to decide: a goal planned again plans the rest.
        # Nor is one removed from the goal, by an operator or by a plan before this one.
        under_way = removed | {link["ref"] for link in links if link["task_id"] or link.get("split_into") is not None}
        return [
            i
            for i in found.values()
            if i.state == "open" and i.ref not in under_way and holders.get(i.ref, goal["id"]) == goal["id"]
        ]

    async def plan_one(goal: dict, who: str) -> bool:
        """Start a planning goal's planning task, over the issues the goal already has. A failure is logged:
        Replan on its page starts it again. A goal that plans over issues that exist, and has none open yet, is
        not planned: a planning run would spend on nothing. It goes back to not started, and False comes back."""
        try:
            issues = await issues_to_plan(goal)
            if not issues and goal["modality"] not in store.PLANNED_MODALITIES and not goal.get("plan_approved_at"):
                await asyncio.to_thread(move_with, goal["id"], who, "paused", {"planning"})
                log.info("goal %s: no open issue to plan over yet, so no planning task started", goal["id"])
                return False
            await planning.start(goal, tasks, events, who, issues=issues)
        except (planning.PlanError, TrackerError) as err:
            log.error(
                "goal %s: its planning task could not start: %s. Replan on the goal's page starts it again",
                goal["id"],
                err,
            )
        return True

    async def suggest(goal: dict) -> None:
        """A planning goal's suggested fix: started for its latest plan when that has gaps a change to the plan
        would settle, and collected once its planning task ends. A failure is logged and leaves the plan as it
        is: the operator can still approve it or ask for a new one."""
        try:
            await suggestions.collect(goal, tasks, events)

            def latest() -> dict | None:
                with db.connect(autocommit=True) as conn:
                    return plans.latest(conn, goal["id"])

            plan = await asyncio.to_thread(latest)
            if plan and not goal.get("plan_task_id"):
                await suggestions.maybe_start(goal, plan, tasks, events, issues_to_plan, fix_assessor)
        except (suggestions.SuggestionError, TrackerError) as err:
            log.error("goal %s: the planner could not suggest a fix for its plan: %s", goal["id"], err)

    async def update_goals(only: list[dict] | None = None) -> None:
        """The planner's update for each goal whose state has changed since its last. One that cannot be
        written is logged when its reason changes and tried again at the next pass: an update never stops a
        sweep, and the goal's page keeps the one before it."""
        for goal in only if only is not None else await listed(updates.goals, "updates"):
            try:
                await updates.write(goal, tasks, events, update_writer)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - logged; the next pass tries again
                said = f"{type(err).__name__}: {err}"
                if app.state.update_errors.get(goal["id"]) != said:
                    log.error(
                        "goal %s: the planner's update could not be written, and is tried again: %s", goal["id"], said
                    )
                app.state.update_errors[goal["id"]] = said
            else:
                app.state.update_errors.pop(goal["id"], None)

    def planning_goals() -> list[dict]:
        with app.state.pool.connection() as conn:
            return store.list_goals(conn, ["planning"])

    async def follow_up_one(goal: dict) -> SweepResult:
        async with locks.setdefault(goal["id"], asyncio.Lock()):
            return await follow_up_goal(goal, tracker, tasks, events, host)

    def swept(goal_id: str, errors: list[str]) -> None:
        """Keep what a goal's latest sweep or follow-up could not do, for its page and GET /v1/supervisor, and
        log it when it changes: every minute's sweep failing the same way is one line, not one a minute."""
        before = (app.state.sweeps.get(goal_id) or {}).get("errors")
        app.state.sweeps[goal_id] = {"at": datetime.now(UTC).isoformat(), "errors": errors}
        if errors and errors != before:
            log.warning("goal %s: its sweep could not finish: %s", goal_id, "; ".join(errors)[:1000])

    async def each(goals: list[dict], one, what: str) -> None:
        """Run one for each goal, one goal's failure never stopping the next goal's turn. A sweep used to be
        wrapped as a whole pass with its exceptions dropped, so one goal that raised left every goal after
        it unswept, with nothing to say so."""
        for goal in goals:
            try:
                result = await one(goal)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - recorded on the goal; the next goal still gets its turn
                swept(goal["id"], [f"the {what} failed: {type(err).__name__}: {err}"])
                continue
            swept(goal["id"], list(getattr(result, "errors", None) or []))

    async def listed(lister, what: str) -> list[dict]:
        try:
            return await asyncio.to_thread(lister)
        except Exception as err:  # noqa: BLE001 - logged; the next pass lists again
            log.error("planner: could not list the goals for the %s: %s: %s", what, type(err).__name__, err)
            return []

    async def sweep_forever() -> None:
        followed_up_at: float | None = None
        while True:
            await each(await listed(swept_goals, "sweep"), sweep_one, "sweep")
            # A goal no sweep covers, a met one most of all, keeps its issues' labels and its canceled tasks'
            # pull request ends true for costs by label and by outcome, less often.
            now = asyncio.get_running_loop().time()
            if followed_up_at is None or now - followed_up_at >= FOLLOW_UP_SECONDS:
                followed_up_at = now
                await each(await listed(followed_up_goals, "follow-up"), follow_up_one, "follow-up")
                # The canceled tasks of no goal, which no goal's sweep lists.
                try:
                    await follow_up_goalless(tasks, events, host)
                except Exception as err:  # noqa: BLE001 - logged; the next follow-up tries again
                    log.error("planner: the follow-up of tasks of no goal failed: %s: %s", type(err).__name__, err)

            # A planning goal's ended planning task becomes its plan.
            async def collect(goal: dict) -> None:
                await planning.collect(goal, tasks, events)
                await suggest(goal)

            await each(await listed(planning_goals, "plans"), collect, "collecting of its plan")
            await update_goals()
            # Tasks with no short name yet get one. A failure leaves them showing their titles.
            try:
                await naming.name_unnamed(tasks, events, namer)
            except Exception as err:  # noqa: BLE001 - logged; the next pass names them
                log.error(
                    "planner: the naming of tasks failed: %s: %s. The next pass tries again", type(err).__name__, err
                )
            await asyncio.sleep(interval)

    supervisor = Supervisor(tasks, events, workflows or TemporalWorkflows(), host)
    supervise_seconds = supervisor_seconds()

    async def supervise_forever() -> None:
        """Every MH_SUPERVISOR_SECONDS: look at every open task, and move the ones no one has. What a pass
        found is kept for GET /v1/supervisor, and a pass that fails is logged and said there, never dropped:
        the supervisor is what notices the rest, so its own trouble has to show."""
        while True:
            started = datetime.now(UTC)
            try:
                found = await supervisor.pass_once()
                app.state.supervised = {"at": started.isoformat(), "error": None, **asdict(found)}
                for error in found.errors:
                    log.warning("supervisor: %s", error)
            except Exception as err:  # noqa: BLE001 - the next pass tries again; this one is on the record
                log.error("supervisor: the pass failed: %s: %s", type(err).__name__, err)
                last = getattr(app.state, "supervised", None) or {}
                app.state.supervised = {
                    **last,
                    "failed_at": started.isoformat(),
                    "error": f"{type(err).__name__}: {err}",
                }
            await asyncio.sleep(supervise_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pool = db.pool(database_url)
        if config_store.from_database():
            await asyncio.to_thread(add_shipped_configuration, app.state.pool, events)
        loop = asyncio.create_task(sweep_forever()) if interval > 0 else None
        titling = asyncio.create_task(title_untitled()) if interval > 0 else None
        # The cost sources, harvested beside the sweeps: off wherever the sweeps are (tests).
        harvesting = asyncio.create_task(app.state.harvest_costs()) if interval > 0 else None
        # The supervisor runs beside the sweeps, and is off wherever they are (tests).
        supervising = asyncio.create_task(supervise_forever()) if interval > 0 and supervise_seconds > 0 else None
        try:
            yield
        finally:
            if loop:
                loop.cancel()
            if titling:
                titling.cancel()
            if harvesting:
                harvesting.cancel()
            if supervising:
                supervising.cancel()
            app.state.pool.close()

    app = FastAPI(title="meta-harness planner", lifespan=lifespan)
    app.state.supervised = None
    # What each goal's latest sweep, follow-up or plan collecting could not do: {goal_id: {at, errors}}.
    app.state.sweeps = {}
    app.state.goal_costs = goal_costs.Cache(tasks, events)
    # Why each goal's latest update could not be written, for its page; empty when it was.
    app.state.update_errors = {}
    app.state.each = each
    app.state.supervisor = supervisor
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

    def change_with(goal_id: str, who: str, event_extra: dict | None = None, **fields: Any) -> tuple[dict, dict]:
        """Change a goal and record goal_changed, and issue_dropped for each issue with no task that a change of
        repos let go. Returns (goal, the fields that changed with their old values). event_extra is added to
        the goal_changed payload."""
        dropped: list[str] = []
        try:
            with app.state.pool.connection() as conn:
                goal, changed = store.update(conn, goal_id, dropped=dropped, **fields)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail="no such goal") from err
        except store.GoalError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        if changed:
            events.emit(
                "goal_changed",
                {
                    "goal_id": goal_id,
                    "changes": {k: {"from": str(v), "to": str(goal[k])} for k, v in changed.items()},
                    **(event_extra or {}),
                },
                actor=who,
            )
        for ref in dropped:
            events.emit("issue_dropped", {"ref": ref, "goal_id": goal_id}, actor=who, tags={"goal_id": goal_id})
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
    sizing.check_settings()
    costs_api.check_settings()
    planning.check_settings()
    writing_rules.check_settings()
    charter.check_settings()
    draft = drafter or drafting.draft_goal

    def registered(names: list[str]) -> list[str]:
        """The registry's names for the repos asked for, or every registered repo. 404 for an unknown one."""
        if not names:
            return [r.name for r in repos.registry().values()]
        unknown = [name for name in names if repos.get(name) is None]
        if unknown:
            raise HTTPException(status_code=404, detail=f"not in the repo registry: {', '.join(unknown)}")
        return [repos.get(name).name for name in names]

    def checked_refs(
        refs: list[str], goal_repos: list[str], scheme: str | None = None, settings: dict | None = None
    ) -> list[tuple[str, str]]:
        """Each ref with its repo's registry name. 422 for a malformed ref, one outside the goal's repos, or one
        of another tracker than the goal's (scheme): a goal's issues are all in one tracker. settings: what
        the goal told its tracker, which says whether an issue is one of its (a Jira issue of its project)."""
        allowed = {name.lower(): name for name in goal_repos}
        source = bound_tracker(scheme, goal_repos, settings) if hasattr(tracker, "named") else tracker
        pairs, wrong = [], []
        for ref in dict.fromkeys(refs):
            # A task kept in the meta-harness may join any goal, whatever its tracker.
            kept = hasattr(tracker, "adapter") and ref.startswith(f"{tracker_registry.OWN.scheme}:")
            if scheme and hasattr(tracker, "named") and not kept and not ref.startswith(f"{scheme}:"):
                wrong.append(f"{ref} is not a {scheme} issue, and the goal's tracker is {scheme}")
                continue
            try:
                # The tracker the ref names says which repo the issue's work is done in.
                reader = tracker.named(tracker_registry.OWN.scheme) if kept else source
                repo = reader.repo_of(ref) if hasattr(reader, "repo_of") else parse_ref(ref)[0]
            except (ValueError, TrackerError) as err:
                wrong.append(
                    f"{ref} cannot be one of the goal's issues: {err}. A ref is github:<owner>/<repo>#<number>, "
                    "or jira:<PROJECT>-<number>"
                )
                continue
            if repo.lower() not in allowed:
                wrong.append(f"{ref} is in {repo}, which is not one of the goal's repos")
                continue
            # The repo's name as the registry spells it, so one issue has one ref whatever case was typed.
            if ref.startswith("github:"):
                ref = f"github:{allowed[repo.lower()]}#{parse_ref(ref)[1]}"
            pairs.append((ref, allowed[repo.lower()]))
        if wrong:
            raise HTTPException(status_code=422, detail="; ".join(wrong))
        return pairs

    def add(goal: dict, who: str, refs: list[str]) -> list[str]:
        """Hold refs for the goal, and record each one held as issue_added. 409 for a ref another goal holds."""
        pairs = checked_refs(refs, goal["repos"], goal.get("tracker") or "github", goal.get("tracker_settings"))
        try:
            with app.state.pool.connection() as conn:
                added = store.add_refs(conn, goal["id"], pairs)
        except store.GoalError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        for ref in added:
            events.emit("issue_added", {"ref": ref, "goal_id": goal["id"]}, actor=who, tags={"goal_id": goal["id"]})
        return added

    def goal_settings(given: str | None) -> dict:
        """A tracker's goal settings from a query string's JSON. 422 for anything but an object."""
        if not given:
            return {}
        try:
            parsed = json.loads(given)
        except ValueError:
            parsed = None
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=422, detail='settings is a JSON object, such as {"project": "PROJ"}')
        return parsed

    def tracker_named(scheme: str | None):
        """The tracker by name, the default when none is named; the one tracker given, for a planner built
        with a single one. 422, saying which are set up, for one that is not."""
        if not hasattr(tracker, "named"):
            return tracker
        chosen = scheme or tracker_registry.default()
        if tracker_registry.settings(chosen) is None:
            set_up = ", ".join(sorted(tracker_registry.document().get("trackers") or {})) or "none"
            raise HTTPException(
                status_code=422,
                detail=f"the tracker {chosen!r} is not set up (set up: {set_up}). Set it up under Configuration, "
                "Issue trackers, or choose one of those.",
            )
        try:
            return tracker.named(chosen)
        except TrackerError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err

    def bound_tracker(scheme: str | None, names: list[str], settings: dict | None):
        """The tracker by name for a goal, or a goal to be: its repos and what it tells the tracker. 422 when
        what it tells cannot be used, saying why."""
        chosen = tracker_named(scheme)
        if not hasattr(chosen, "bound"):
            return chosen
        spec = tracker_registry.spec(getattr(chosen, "scheme", ""))
        if spec and (reasons := spec.check_goal(dict(settings or {}))):
            raise HTTPException(status_code=422, detail="; ".join(reasons))
        return chosen.bound(Source(tuple(names), dict(settings or {})))

    async def open_issues(
        names: list[str], label: str | None, scheme: str | None = None, settings: dict | None = None
    ) -> dict[str, list]:
        source = bound_tracker(scheme, names, settings)
        try:
            found = await asyncio.gather(*(source.list_open(name, label) for name in names))
        except TrackerError as err:
            raise HTTPException(status_code=502 if err.retryable else 422, detail=str(err)) from err
        return dict(zip(names, found, strict=True))

    # The configuration API, for every kind, until the run service has an HTTP API of its own.
    def in_use(kind: str, document: dict[str, Any]) -> list[str]:
        """What an open goal names stays in the configuration, and the refusal names the goals: a repo in the
        registry, and in the trackers what the goal told its tracker, such as its Jira project."""
        if kind == "trackers":
            with app.state.pool.connection() as conn:
                goals = store.list_goals(conn, list(store.OPEN_GOAL_STATUSES))
            reasons = []
            for goal in goals:
                scheme = goal.get("tracker") or "github"
                spec, site = tracker_registry.SUPPORTED.get(scheme), (document.get("trackers") or {}).get(scheme)
                if spec is None or not isinstance(site, dict):
                    continue  # A tracker that is not set up is refused by the document's own check.
                name = f"open goal {goal['id']} ({goal.get('title') or goal['statement'][:60]})"
                reasons += [
                    f"{scheme}: {why}: {name} uses it"
                    for why in spec.check_goal(goal.get("tracker_settings") or {}, site)
                ]
            return reasons
        if kind != "repos":
            return []
        kept = {name.lower() for name in (document.get("repos") or {})}
        with app.state.pool.connection() as conn:
            goals = store.list_goals(conn, list(store.OPEN_GOAL_STATUSES))
        reasons = []
        for goal in goals:
            for repo in goal["repos"]:
                if repo.lower() not in kept:
                    reasons.append(
                        f"{repo} is named by open goal {goal['id']} ({goal.get('title') or goal['statement'][:60]})"
                    )
        return reasons

    app.include_router(config_api.router(lambda: app.state.pool, operator, events, in_use))

    @app.get("/health")
    def health() -> dict[str, str]:
        with app.state.pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/v1/repos")
    def registered_repos(who: str = Depends(operator)) -> dict:
        """The repo registry: the repos a goal may name."""
        return {"repos": [{"name": r.name, "tracker": r.tracker} for r in repos.registry().values()]}

    async def task_pull(task_id: str) -> tuple[dict, str, int]:
        """A task with its pull request's repo and number. 404 when the task has none."""
        try:
            task = await asyncio.to_thread(tasks.get, task_id)
        except TaskApiError as err:
            raise HTTPException(status_code=404 if err.status == 404 else 502, detail=str(err)) from err
        if not task.get("pr_url"):
            raise HTTPException(status_code=404, detail="the task has no pull request")
        try:
            repo, number = code_host.pr_number(task["pr_url"])
        except code_host.CodeHostError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err
        return task, repo, number

    @app.get("/v1/pulls/{task_id}/attempts")
    async def attempt_compare(task_id: str, who: str = Depends(operator)) -> dict:
        """A task's attempts side by side: each one's candidate, how it ended, its cost, and its changes from its own
        workspace. waiting says the task waits for an operator to pick one."""
        try:
            task = await asyncio.to_thread(tasks.get, task_id)
        except TaskApiError as err:
            raise HTTPException(status_code=404 if err.status == 404 else 502, detail=str(err)) from err
        try:
            page = await asyncio.to_thread(events.read, 0, 500, task_id=task_id, event_type="attempt_finished")
        except Exception as err:  # noqa: BLE001 - the event service is the caller's 502
            raise HTTPException(status_code=502, detail=f"could not read the task's attempts: {err}") from err
        shown = []
        for event in page["events"]:
            p = event["payload"]
            entry = {
                "attempt": p.get("attempt"),
                "run_id": event.get("run_id"),
                "candidate": p.get("candidate"),
                "decision": p.get("decision"),
                "detail": p.get("detail"),
                "spent_usd": p.get("spent_usd"),
                "files": [],
                "patch": "",
            }
            if p.get("decision") == "finished" and event.get("run_id"):
                try:
                    entry |= await code_host.workspace_diff(task_id, event["run_id"])
                except Exception as err:  # noqa: BLE001 - one unreadable workspace says why and shows the rest
                    entry["patch"] = f"could not read the workspace: {err}"
            shown.append(entry)
        waiting = (
            task["status"] == "running" and not task.get("pr_url") and "pick one" in (task.get("status_reason") or "")
        )
        return {"task_id": task_id, "waiting": waiting, "attempts": shown}

    @app.get("/v1/pulls/{task_id}/diff")
    async def pull_diff(task_id: str, who: str = Depends(operator)) -> dict:
        """The files the task's pull request changes against its base, with their patches, and its head commit.
        Served here with run service code, as the configuration API is, until the run service has an HTTP API."""
        _, repo, number = await task_pull(task_id)
        try:
            pull = await code_host.read_pull_request(repo, number)
            files = await code_host.pull_request_files(repo, number)
        except code_host.CodeHostError as err:
            raise HTTPException(status_code=502, detail=str(err)) from err
        return {"repo": repo, "number": number, "head_sha": pull.head_sha, "state": pull.state, "files": files}

    # How long each of a repo's checks took on its default branch's newest commit, kept a while: what a wait for
    # checks usually lasts, read once for every task that asks.
    usual_checks: dict[str, tuple[float, dict[str, int]]] = {}

    async def usual_seconds(repo: str) -> dict[str, int]:
        kept = usual_checks.get(repo)
        if kept and time.monotonic() - kept[0] < USUAL_CHECKS_SECONDS:
            return kept[1]
        try:
            found = await code_host.check_details(repo, "HEAD")
        except code_host.CodeHostError as err:
            log.warning("could not read %s's usual check times: %s", repo, err)
            return kept[1] if kept else {}
        usual = {check["name"]: check["seconds"] for check in found if check["seconds"] is not None}
        usual_checks[repo] = (time.monotonic(), usual)
        return usual

    @app.get("/v1/pulls/{task_id}/checks")
    async def pull_checks(task_id: str, who: str = Depends(operator)) -> dict:
        """The code host's checks on the commit the task waits on, for an operator to watch: its pull request's
        head, or its merge commit once merged (`of`). Each check has its state, times and log, and `usual_seconds`,
        how long it took on the default branch's newest commit. `state` is how they stand together, and
        `usual_seconds` how long the wait usually is: the slowest of these checks'. 404 for a task with no pull request,
        502 when the code host cannot be read."""
        _, repo, number = await task_pull(task_id)
        try:
            pull = await code_host.read_pull_request(repo, number)
            sha = pull.merge_sha or pull.head_sha
            checks = await code_host.check_details(repo, sha) if sha else []
            # A merged task's run that a newer merge canceled or replaced counts by the newer run, as the task's
            # own wait counts it.
            if pull.merge_sha and checks:
                checks = await merge_checks.judged(repo, pull.merge_sha, checks)
        except code_host.CodeHostError as err:
            raise HTTPException(status_code=502, detail=str(err)) from err
        usual = await usual_seconds(repo)
        for check in checks:
            check["usual_seconds"] = usual.get(check["name"])
        done = [c for c in checks if c["status"] == "completed"]
        failed = [c["name"] for c in done if c["conclusion"] not in code_host.PASSING_CONCLUSIONS]
        state = "none" if not checks else "failed" if failed else "pending" if len(done) < len(checks) else "passed"
        return {
            "repo": repo,
            "number": number,
            "sha": sha,
            "of": "merge" if pull.merge_sha else "pull_request",
            "state": state,
            "passed": len(done) - len(failed),
            "failed": failed,
            "total": len(checks),
            # Of the checks on this commit only: the default branch runs some a pull request does not (a deploy).
            "usual_seconds": max((c["usual_seconds"] for c in checks if c["usual_seconds"] is not None), default=None),
            "checks": checks,
        }

    @app.post("/v1/pulls/{task_id}/comments", status_code=201)
    async def pull_comment(task_id: str, body: LineCommentIn, who: str = Depends(operator)) -> dict:
        """A review comment on lines of the pull request, posted as the GitHub App, its text naming the operator."""
        _, repo, number = await task_pull(task_id)
        try:
            pull = await code_host.read_pull_request(repo, number)
            signed = Provenance(COMMENT, task=task_id, by=who)
            url = await code_host.comment_on_lines(
                repo,
                number,
                pull.head_sha,
                body.path,
                body.line,
                # The operator's own words: redacted and cut to fit, never rewritten.
                await outbound.prepare(
                    f"**{who}** wrote:\n\n{body.body}",
                    outbound.Context(outbound.GITHUB, outbound.COMMENT, outbound.PERSON),
                ),
                body.start_line,
                provenance=signed,
            )
        except code_host.CodeHostError as err:
            raise HTTPException(status_code=422 if err.status == 422 else 502, detail=str(err)) from err
        comment = {"path": body.path, "line": body.line, "start_line": body.start_line, "body": body.body, "url": url}
        events.emit("pull_request_commented", {**comment, "provenance": payload(signed)}, task_id=task_id, actor=who)
        return comment

    @app.get("/v1/search")
    def search(q: str = Query(min_length=1, max_length=200), who: str = Depends(operator)) -> dict:
        """Goals and tasks whose text holds q, for the console's command palette. Issues come in as their tasks:
        a task's title is its issue's. The planner reads its own goals and asks the Task API for tasks."""
        with app.state.pool.connection() as conn:
            goals = store.search_goals(conn, q)
        try:
            found = tasks.search(q)
        except (TaskApiError, AttributeError):
            found = []
        return {
            "goals": [
                {"id": g["id"], "title": g.get("title") or g["statement"][:80], "status": g["status"]} for g in goals
            ],
            "tasks": [
                {"id": t["id"], "title": t["title"], "status": t["status"], "ref": t.get("external_ref")} for t in found
            ],
        }

    @app.get("/v1/me/settings")
    def my_settings(who: str = Depends(operator)) -> dict:
        """The signed-in user's own settings, or the defaults when they have saved none."""
        with app.state.pool.connection() as conn:
            return settings.read(conn, who).model_dump(mode="json")

    @app.put("/v1/me/settings")
    def save_my_settings(body: settings.UserSettings, who: str = Depends(operator)) -> dict:
        """Save the signed-in user's own settings. Each user writes only their own row."""
        unknown = [name for name in body.planner.repos if repos.get(name) is None]
        if unknown:
            raise HTTPException(status_code=422, detail=f"not in the repo registry: {', '.join(unknown)}")
        with app.state.pool.connection() as conn:
            return settings.write(conn, who, body).model_dump(mode="json")

    @app.get("/v1/repos/available")
    async def available_repos(who: str = Depends(operator)) -> dict:
        """The repos the deployment's GitHub Apps are installed on and the registry does not list yet, and where
        to install an app on more."""
        try:
            installed = await apps.installed()
        except Exception as err:  # noqa: BLE001 - say why; the page still lists the registry
            raise HTTPException(status_code=502, detail=f"could not list the GitHub Apps' repos: {err}") from err
        registered = {name.lower() for name in repos.registry()}
        return {
            "repos": sorted(
                (
                    {"name": r["name"], "private": r["private"]}
                    for r in installed
                    if r["name"].lower() not in registered
                ),
                key=lambda r: r["name"].lower(),
            ),
            "install_urls": sorted({r["install_url"] for r in installed}),
        }

    @app.get("/v1/trackers")
    def list_trackers(who: str = Depends(operator)) -> dict:
        """Every tracker the meta-harness supports, as its adapter describes it: its settings, for the console's
        form; whether it is set up and signed in; and where its issues are read, for a link to one. `default` is
        the tracker a new goal uses when it names none."""
        document = tracker_registry.document()
        set_up = document.get("trackers") or {}
        found = []
        own = tracker_registry.OWN
        for scheme, spec in [*tracker_registry.SUPPORTED.items(), (own.scheme, own)]:
            adapter = tracker.adapter(scheme) if hasattr(tracker, "adapter") else None
            missing = [name for name in spec.credentials if not os.environ.get(name)]
            found.append(
                {
                    "scheme": scheme,
                    "title": spec.title,
                    "description": spec.description,
                    "settings": [asdict(setting) for setting in spec.settings],
                    # What a goal that uses it says besides its repos, for the New goal form.
                    "goal_settings": [asdict(setting) for setting in spec.goal_settings],
                    # What a new goal starts with for them, from the tracker's settings: Jira's default project.
                    "goal_defaults": spec.goal_defaults(set_up.get(scheme)) if scheme in set_up else {},
                    "credentials": spec.credentials,
                    # The meta-harness's own tracker has nothing to set up: it is the choice of no tracker.
                    "configured": scheme in set_up or spec is own,
                    "built_in": spec is own,
                    # Signed in: every secret its sign-in needs is in the planner's environment.
                    "signed_in": not missing,
                    "missing_credentials": missing,
                    "writes_progress": spec.writes_progress,
                    # The start of an issue's address, when the tracker's issues have one place: the console
                    # adds the issue's key. Null for a tracker whose address depends on the repo.
                    "issue_url_prefix": getattr(adapter, "issue_url_prefix", lambda: None)(),
                }
            )
        return {"default": tracker_registry.default(), "trackers": found}

    @app.get("/v1/trackers/{scheme}/choices/{setting}")
    async def tracker_choices(scheme: str, setting: str, request: Request, who: str = Depends(operator)) -> dict:
        """What the console offers for one of a tracker's settings, as value and label, so a name is picked and
        not typed. The query says what the setting belongs to, which the adapter reads: Jira's `projects` (every
        project of the site) and `project` (the listed ones, for a goal) take none; its `issue_type` and
        `statuses` take `project`, and list that project's own. Empty for a setting with nothing to list. 422
        when the tracker is not set up, 502 when it cannot be read, each saying why."""
        adapter = tracker_named(scheme)
        if not hasattr(adapter, "choices"):
            return {"choices": []}
        try:
            return {"choices": await adapter.choices(setting, dict(request.query_params))}
        except TrackerError as err:
            raise HTTPException(status_code=502 if err.retryable else 422, detail=str(err)) from err

    @app.get("/v1/labels")
    async def labels(
        repo: str | None = None,
        tracker: str | None = None,
        settings: str | None = None,
        who: str = Depends(operator),
    ) -> dict:
        """Every label on the open issues of the registered repos, or of one, with its issue counts and how many an
        open goal holds. tracker: which tracker's issues, the default's when left out; settings: what that
        tracker asks of a goal, as JSON (for Jira, {"project": "PROJ"})."""
        names = registered([repo] if repo else [])
        issues = await open_issues(names, None, tracker, goal_settings(settings))
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
        tracker: str | None = None,
        settings: str | None = None,
        who: str = Depends(operator),
    ):
        """A registered repo's open issues, with the label if given and the text q in their titles if given,
        newest first, a page at a time from cursor. Each names the open goal that holds it, if any. tracker:
        which tracker's issues; the default's when left out."""
        [repo] = registered([f"{owner}/{name}"])
        issues = (await open_issues([repo], label, tracker, goal_settings(settings)))[repo]
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
        """A goal drafted by a model from the open issues of one label or of several. Nothing is saved: the
        operator edits it first. A goal takes one label, so a draft from several comes back with `label` null
        and the issues it read in `refs`, for the goal to hold as picked issues."""
        labels = list(dict.fromkeys([name.strip() for name in [*body.labels, body.label or ""] if name.strip()]))
        if not labels:
            raise HTTPException(status_code=422, detail="name the label to draft from, in label, or several in labels")
        names = registered(body.repos)
        by_ref: dict[str, Any] = {}
        for name in labels:
            for found in (await open_issues(names, name, body.tracker, body.tracker_settings)).values():
                by_ref.update({i.ref: i for i in found if i.ref not in by_ref})
        issues = list(by_ref.values())
        # What the model is told the label is, and what the draft is recorded under.
        said = ", ".join(labels)
        if body.refs:
            wanted = set(body.refs)
            issues = [i for i in issues if i.ref in wanted]
        try:
            drafted = await draft(said, issues)
        except drafting.DraftError as err:
            raise HTTPException(status_code=502, detail=str(err)) from err
        events.emit(
            "goal_drafted",
            {
                "label": said,
                "repos": names,
                "issues": len(issues),
                "model": drafted.model,
                "usd_cost": drafted.usd_cost,
            },
            actor=who,
        )
        return {
            **asdict(drafted),
            "label": labels[0] if len(labels) == 1 else None,
            "labels": labels,
            "repos": names,
            "refs": [i.ref for i in issues],
        }

    @app.post("/v1/goals", status_code=201)
    async def create(body: GoalIn, who: str = Depends(operator)) -> dict:
        unknown = [name for name in body.repos if repos.get(name) is None]
        if unknown:
            raise HTTPException(status_code=422, detail=f"not in the repo registry: {', '.join(unknown)}")
        names = [repos.get(name).name for name in body.repos]
        planned = body.modality in store.PLANNED_MODALITIES
        # A goal that plans its own issues needs neither: its plan's issues get a label of the goal's own. Nor
        # does one that takes the issues assigned to the meta-harness in its tracker.
        chosen_tracker = body.tracker or tracker_registry.default()
        spec = tracker_registry.spec(chosen_tracker)
        if spec:
            # What the goal does not say starts as its tracker's default: Jira's default project.
            defaults = spec.goal_defaults(tracker_registry.settings(chosen_tracker))
            body.tracker_settings = {**defaults, **body.tracker_settings}
        assigned = bool(spec and spec.takes_assigned(body.tracker_settings))
        if chosen_tracker == tracker_registry.OWN.scheme and not planned:
            raise HTTPException(
                status_code=422,
                detail="a goal that uses no issue tracker has no issues to take in: its work is planned from its "
                "description. Choose to plan it from the description, or choose a tracker for the goal",
            )
        if not body.label and not body.refs and not planned and not assigned:
            raise HTTPException(
                status_code=422,
                detail="a goal needs issues to take in: give it a label, or pick issues by ref. Where its "
                "tracker supports it, you can also have the goal take the issues assigned to the meta-harness",
            )
        if why := parallel_reason(body.max_parallel):
            raise HTTPException(status_code=422, detail=why)
        # One tracker for all the goal's issues: the one it names, else the default, fixed from here on.
        scheme = getattr(tracker_named(body.tracker), "scheme", "") or None
        # What the tracker asks of a goal is checked before the goal exists: a Jira goal names its project.
        bound_tracker(scheme, names, body.tracker_settings)
        pairs = checked_refs(body.refs, names, scheme, body.tracker_settings)
        fields = {
            **body.model_dump(exclude={"refs", "autonomy"}),
            # full is auto_merge on, whichever of the two the caller sent.
            "auto_merge": body.auto_merge if body.autonomy is None else body.autonomy == "full",
            "tracker": scheme,
            "tracker_settings": body.tracker_settings or None,
            # Planned before any task runs: a goal that plans its issues from its description always, and one
            # that takes existing issues when the planning configuration says every goal is.
            "planned": planned or planning.plans_every_goal(),
        }
        try:
            with app.state.pool.connection() as conn:
                goal = store.create(conn, who, store.NewGoal(**{**fields, "repos": names}))
                if body.autonomy == "ask":
                    goal, _ = store.update(conn, goal["id"], autonomy="ask")
                held = store.add_refs(conn, goal["id"], pairs)
                if planned and not goal["label"]:
                    goal, _ = store.update(conn, goal["id"], label=planning.goal_label(goal))
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
                "strict_issues": goal["strict_issues"],
                "tracker": goal["tracker"],
                "tracker_settings": goal["tracker_settings"],
                "start": body.start,
            },
            actor=who,
        )
        for ref in held:
            events.emit("issue_added", {"ref": ref, "goal_id": goal["id"]}, actor=who, tags={"goal_id": goal["id"]})
        if goal["status"] == "active":
            asyncio.create_task(sweep_one(goal))
        if goal["status"] == "planning":
            # Before the answer goes back, so the goal it shows already names its planning task.
            await plan_one(goal, who)
            goal = await asyncio.to_thread(load, goal["id"])
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
        standings(goals)
        # What each goal has cost, the same total as its Spend section; unknown, with why, when unreadable.
        goal_costs.give(goals, *app.state.goal_costs.get())
        return {"goals": goals, "total": total, "offset": offset, "limit": limit}

    def standings(goals: list[dict]) -> None:
        """Give each goal its `standing`: whether it is moving, held, or waits on an operator, from where its
        tasks stand in the supervisor's record. One read of the open tasks serves every goal asked for. A
        Task API that does not answer leaves the standing out: the goal is still shown."""
        wanted = [goal for goal in goals if goal["status"] not in ("met", "canceled")]
        for goal in goals:
            goal["standing"] = goal_standing(goal["status"], []) if goal not in wanted else None
        if not wanted:
            return
        try:
            found = cost_facts.every_task(tasks, None, list(OPEN_STATUSES))
        except Exception as err:  # noqa: BLE001 - a goal's page does not fail for want of its standing
            log.warning("could not read the tasks for the goals' standings: %s", err)
            return
        by_goal: dict[str, list[dict]] = {}
        for task in found:
            by_goal.setdefault(task.get("goal_id") or "", []).append(task)
        with app.state.pool.connection() as conn:
            for goal in wanted:
                refused = sum(
                    1 for link in store.links(conn, goal["id"]) if link["refused_reason"] and link["state"] == "open"
                )
                goal["standing"] = goal_standing(goal["status"], by_goal.get(goal["id"], []), refused)

    # For each goal no sweep covers: when its issues' states were last read from the tracker, and whether
    # every one could be read.
    checked: dict[str, tuple[float, str, bool]] = {}

    async def current_states(goal: dict) -> dict:
        """For a goal no sweep covers, read its open issues' states from the tracker, at most once every
        STATE_CHECK_SECONDS, so its page never shows the state its last sweep saw as today's. Returns when
        the states were last read and whether every one could be; nothing for a swept goal, whose sweep
        keeps them."""
        if goal["status"] in SWEPT_STATUSES:
            return {"states_checked_at": None, "states_complete": True}
        now = asyncio.get_running_loop().time()
        last = checked.get(goal["id"])
        if last is None or now - last[0] >= STATE_CHECK_SECONDS:
            errors = await refresh_states(goal, tracker, events)
            for error in errors:
                log.warning("goal %s: could not read an issue's state: %s", goal["id"], error)
            last = (now, datetime.now(UTC).isoformat(), not errors)
            checked[goal["id"]] = last
        return {"states_checked_at": last[1], "states_complete": last[2]}

    @app.get("/v1/goals/{goal_id}")
    async def read(goal_id: str, who: str = Depends(operator)) -> dict:
        goal = await asyncio.to_thread(load, goal_id)
        goal.update(await current_states(goal))

        def links() -> list[dict]:
            with app.state.pool.connection() as conn:
                return store.links(conn, goal_id)

        goal["issues"] = await asyncio.to_thread(links)
        # When the planner last swept the goal, and what that sweep could not do: empty when it did all of it.
        goal["sweep"] = app.state.sweeps.get(goal_id)
        await asyncio.to_thread(standings, [goal])
        # What the goal has cost, the one figure every page shows for it: the same as on the goal list.
        goal_costs.give([goal], *await asyncio.to_thread(app.state.goal_costs.get))
        return goal

    @app.get("/v1/goals/{goal_id}/updates")
    async def goal_updates(goal_id: str, who: str = Depends(operator)) -> dict:
        """The planner's updates on the goal, newest first, whether it writes them, and why the latest could
        not be written when it could not."""
        await asyncio.to_thread(load, goal_id)
        return {
            "updates": await asyncio.to_thread(updates.latest, goal_id),
            "enabled": bool(updates.settings()["enabled"]),
            "error": app.state.update_errors.get(goal_id),
        }

    @app.post("/v1/goals/{goal_id}/updates")
    async def ask_for_update(goal_id: str, who: str = Depends(operator)) -> dict:
        """Has the planner write an update now, whether or not anything has changed since its last."""
        goal = await asyncio.to_thread(load, goal_id)
        if not updates.settings()["enabled"]:
            raise HTTPException(
                status_code=409,
                detail="the planner's updates are off: turn them on in Configuration, Planning, then ask again",
            )
        try:
            written = await updates.write(goal, tasks, events, update_writer, asked=True)
        except updates.UpdateError as err:
            log.error("goal %s: the update an operator asked for could not be written: %s", goal_id, err)
            raise HTTPException(
                status_code=502, detail=f"the update could not be written: {err}. Ask again in a moment"
            ) from err
        app.state.update_errors.pop(goal_id, None)
        return {"update": written}

    @app.get("/v1/goals/{goal_id}/issues")
    async def goal_issues(goal_id: str, who: str = Depends(operator)) -> dict:
        """Each of the goal's issues with its task's status, pull request URL, pull request state and checks,
        the goal's status, and for a goal no sweep covers, when its issues' states were read from the tracker.

        The task's facts come from the Task API and the events recorded on it, never from the task
        service's tables.
        """
        goal = await asyncio.to_thread(load, goal_id)
        states = await current_states(goal)
        shown = await asyncio.to_thread(issues_view, goal_id)
        return {"goal_id": goal_id, "goal_status": goal["status"], **states, "issues": shown}

    def issues_view(goal_id: str) -> list[dict]:
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
        return progress.issues_progress(links, found, recorded)

    @app.get("/v1/goals/{goal_id}/spend")
    def goal_spend(goal_id: str, who: str = Depends(operator)) -> dict:
        """Where the goal's money went: each of its tasks with what its runs spent, in all and by kind of run
        (the task's own runs, reviews, fixes), most expensive first, and the goal's total against its budget."""
        from meta_harness.tasks import spend

        goal = load(goal_id)
        try:
            found = tasks.list(None, goal_id, TASKS_PAGE)
        except TaskApiError as err:
            raise HTTPException(status_code=502, detail=f"could not read the goal's tasks: {err}") from err
        rows = []
        for task in found:
            try:
                page = events.read(0, 5000, task_id=task["id"], event_type=",".join(spend.SPEND_EVENT_TYPES))
            except Exception as err:  # noqa: BLE001 - an unreachable event service is the caller's 502
                raise HTTPException(status_code=502, detail=f"could not read the tasks' events: {err}") from err
            runs = spend.runs(page["events"])
            rows.append(
                {
                    "task_id": task["id"],
                    "title": task.get("title"),
                    "status": task["status"],
                    "external_ref": task.get("external_ref"),
                    **spend.totals(runs),
                }
            )
        rows.sort(key=lambda row: Decimal(row["usd"]), reverse=True)
        by_kind = {kind: str(sum((Decimal(r["by_kind"][kind]) for r in rows), Decimal(0))) for kind in spend.KINDS}
        total = str(sum((Decimal(r["usd"]) for r in rows), Decimal(0)))
        return {"goal_id": goal_id, "max_usd": goal["max_usd"], "usd": total, "by_kind": by_kind, "tasks": rows}

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
    def drop_issue(goal_id: str, ref: str, force: bool = False, who: str = Depends(operator)) -> dict:
        """Remove an issue from the goal, whether or not it has a task yet.

        The goal holds the issue no longer: its sweep never takes it in again by its label, it does not
        count toward the goal being met, and another goal may take it. Nothing changes on the tracker. A
        task of the issue that has not ended is canceled: one queued or blocked at once, one running as a
        cancel stops it. A task with a pull request open has work done and waiting, so it is refused (409)
        unless `force` is set; with it the task is canceled and the pull request is left open for the
        operator. Adding the issue back by ref holds it again.
        """
        load(goal_id)
        with app.state.pool.connection() as conn:
            link = store.link(conn, ref)
        if link is None or link["goal_id"] != goal_id or link.get("dropped"):
            raise HTTPException(status_code=404, detail=f"the goal does not hold {ref}")
        ended = None
        if link["task_id"]:
            try:
                task = tasks.get(link["task_id"])
            except TaskApiError as err:
                raise HTTPException(status_code=502, detail=f"could not read the issue's task: {err}") from err
            live = task["status"] in ("queued", "blocked", "running", "taken_over", "ready", "review_unresolved")
            if live and task.get("pr_url") and not force:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"its task {task['id']} has pull request {task['pr_url']} open: its work is done and "
                        "waiting. Removing the issue cancels the task and leaves the pull request open"
                    ),
                )
            if live:
                try:
                    tasks.cancel(task["id"], f"its issue was removed from goal {goal_id}", who)
                except TaskApiError as err:
                    raise HTTPException(status_code=502, detail=f"could not cancel the issue's task: {err}") from err
                ended = task["id"]
        try:
            with app.state.pool.connection() as conn:
                store.drop_ref(conn, goal_id, ref)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail=f"the goal does not hold {ref}") from err
        events.emit("issue_dropped", {"ref": ref, "goal_id": goal_id}, actor=who, tags={"goal_id": goal_id})
        return {"goal_id": goal_id, "dropped": ref, "task_canceled": ended}

    @app.get("/v1/activity")
    def pull_request_activity(days: int = Query(91, ge=1, le=366), who: str = Depends(operator)) -> dict:
        """Every pull request the meta-harness opened in the last days, by state, for the Overview's row."""
        try:
            return activity.activity(cost_facts.every_task(tasks), events, days)
        except TaskApiError as err:
            raise HTTPException(status_code=502, detail=f"could not read the tasks: {err}") from err

    @app.patch("/v1/goals/{goal_id}")
    async def patch(goal_id: str, body: GoalPatch, who: str = Depends(operator)) -> dict:
        """Change only the fields sent. A field that may be empty (label, max_usd, max_parallel, provenance) is
        cleared when sent as null; any other sent as null is left as it is. A change to the budget takes effect at
        once: the goal's standing is worked out again, and its tasks held for the budget look again."""
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        for clearable in ("label", "max_usd", "max_parallel", "provenance"):
            if clearable in body.model_fields_set:
                fields[clearable] = getattr(body, clearable)
        if "max_parallel" in fields and (why := parallel_reason(body.max_parallel)):
            raise HTTPException(status_code=422, detail=why)
        if "repos" in fields:
            unknown = [name for name in fields["repos"] if repos.get(name) is None]
            if unknown:
                raise HTTPException(status_code=422, detail=f"not in the repo registry: {', '.join(unknown)}")
            fields["repos"] = list(dict.fromkeys(repos.get(name).name for name in fields["repos"]))
        goal = await asyncio.to_thread(lambda: change(goal_id, who, **fields))
        if {"max_usd", "budget_mode", "budget_overage_percent"} & set(fields):
            try:
                await budget_api.changed(goal, tasks, events, who)
                goal = await asyncio.to_thread(load, goal_id)
            except Exception as err:  # noqa: BLE001 - the change is saved; the next sweep looks at the budget
                log.warning("goal %s: could not look at its new budget: %s", goal_id, err)
        return goal

    @app.get("/v1/supervisor")
    def supervised(who: str = Depends(operator)) -> dict:
        """What the supervisor's last pass found: when it ran, how many tasks each owner has, what it
        recovered, ended and canceled, and what it could not do. `every_seconds` is how often it runs (0: it
        is off), so a page can tell a late pass from a quiet one."""
        failing = {goal_id: found for goal_id, found in app.state.sweeps.items() if found["errors"]}
        return {"every_seconds": supervise_seconds, "last_pass": app.state.supervised, "sweeps_failing": failing}

    @app.get("/v1/supervisor/remedies")
    def remedies_applied(
        task_id: str | None = None,
        goal_id: str | None = None,
        limit: int = Query(100, ge=1, le=500),
        who: str = Depends(operator),
    ) -> dict:
        """What the supervisor did to unstick tasks, newest first: for one task, one goal, or all. Each is one
        try of one remedy, with why it was needed, what was done and what happens next (supervisor/ledger.py)."""
        return {"remedies": ledger.read(task_id, goal_id, limit)}

    @app.get("/v1/limits")
    def limits(who: str = Depends(operator)) -> dict:
        """The system's parallel limit, which a goal's own inherits and may lower, and the VM's ceiling on it."""
        try:
            ceiling = sandbox_ceiling()
        except ConfigError:
            ceiling = None
        return {
            "max_parallel": system_max_parallel(),
            "max_sandboxes": ceiling,
            # Whether a goal that takes existing issues is planned before its tasks run, for the New goal form.
            "plans_every_goal": planning.plans_every_goal(),
            # Where a plan's new tasks go unless the operator says otherwise at approval.
            "track_new_tasks": planning.tracks_new_tasks(),
        }

    @app.post("/v1/goals/{goal_id}/pause")
    def pause(goal_id: str, who: str = Depends(operator)) -> dict:
        return change(goal_id, who, status="paused")

    @app.post("/v1/goals/{goal_id}/resume")
    async def resume(goal_id: str, who: str = Depends(operator)) -> dict:
        """Make the goal active, but not a met or canceled one: that is reopen. Its first start, for a goal
        saved without starting, sweeps it at once. A goal that plans its own issues, with no approved plan
        yet, goes to planning instead, and its planning task starts."""
        current = load(goal_id)
        if (
            (current.get("planned") or current["modality"] in store.PLANNED_MODALITIES)
            and current["status"] == "paused"
            and not current["plan_approved_at"]
        ):
            goal, _ = await asyncio.to_thread(move_with, goal_id, who, "planning", {"paused"})
            if not await plan_one(goal, who):
                where = (
                    f"Label an open issue `{goal['label']}` in its repos, or pick issues"
                    if goal["label"]
                    else "Pick issues"
                )
                raise HTTPException(
                    status_code=409,
                    detail=f"the goal has no open issue to plan yet. {where} with Add issues, then start it again",
                )
            return await asyncio.to_thread(load, goal_id)
        goal, changed = await asyncio.to_thread(
            move_with, goal_id, who, "active", {"active", "paused", "stalled", "budget_exhausted"}
        )
        if "started_at" in changed:
            asyncio.create_task(sweep_one(goal))
        return goal

    @app.post("/v1/goals/{goal_id}/cancel")
    async def cancel(goal_id: str, who: str = Depends(operator)) -> dict:
        """Cancel the goal, and end what it has not started: its queued and blocked tasks are canceled, and
        its running tasks with no pull request are asked to stop. A task with a pull request open is left for
        the operator. The goal comes back with `tasks`: the task ids canceled, stopping, left, and any the
        task service refused."""
        # Under the goal's sweep lock, so a sweep in progress makes no task after these are ended.
        async with locks.setdefault(goal_id, asyncio.Lock()):
            goal = await asyncio.to_thread(change, goal_id, who, status="canceled")
            done = await cancel_goal_tasks(goal, tasks, who)
        for error in done["errors"]:
            log.warning("goal %s: could not end a task on cancel: %s", goal_id, error)
        return {**goal, "tasks": done}

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
        if goal["status"] not in ("active", "budget_exhausted"):
            raise HTTPException(status_code=409, detail=f"the goal is {goal['status']}")
        return asdict(await sweep_one(goal))

    async def activate(goal_id: str, who: str) -> None:
        """An approved plan's goal becomes active, and its first sweep takes the created issues in."""
        goal, _ = await asyncio.to_thread(move_with, goal_id, who, "active", {"planning"})

        async def start() -> None:
            # The sweep makes the tasks; then the planner says so, in place of its update that asked for approval.
            await sweep_one(goal)
            await update_goals([goal])

        asyncio.create_task(start())

    async def plan_again(goal_id: str, who: str) -> dict:
        """A planned goal that is under way goes back to planning: its running tasks go on, and the scheduler
        starts none of its others until the new plan is approved."""
        goal, _ = await asyncio.to_thread(move_with, goal_id, who, "planning", {"active", "stalled"})
        return goal

    plan_api.register(app, operator, load, events, tracker, tasks, activate, issues_to_plan, plan_again)
    budget_api.register(app, operator, load, change_with, events, tasks)
    sizing_api.register(app, operator, load, events, tracker, sweep_one)
    users.register(app, operator, events)

    def admin(key: str = Depends(auth.bearer), role: str | None = Header(default=None, alias="X-MH-Role")) -> bool:
        """Whether the caller is an admin: an operator key is, and the edge names the role of who it acts for."""
        return bool(auth.label_for(key, operators)) or role == "admin"

    def goal_or_none(goal_id: str) -> dict | None:
        if not goal_id:
            return None
        try:
            with app.state.pool.connection() as conn:
                return store.get(conn, goal_id)
        except store.NotFound:
            return None

    def task_facts() -> dict[str, dict]:
        """Each task's issue labels and pull request outcome, for costs by label and by outcome."""
        found = cost_facts.every_task(tasks)
        ends = cost_facts.pull_request_ends(events)
        with app.state.pool.connection() as conn:
            return cost_facts.task_facts(conn, found, ends)

    app.state.harvest_costs = costs_api.register(app, operator, admin, events, tasks, goal_or_none, task_facts)

    return app
