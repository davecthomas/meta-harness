"""The plan routes of the planner's API: read a feature goal's plan, edit it, ask for a new one, approve it.

    GET  /v1/goals/{id}/plan      the newest plan version, and the planning task in flight if there is one
    PUT  /v1/goals/{id}/plan      save edited drafts as the next version
    POST /v1/goals/{id}/replan    start a new planning task, with a note
    POST /v1/goals/{id}/approve   create the plan's issues and make the goal active
    POST /v1/goals/enrich, /v1/goals/{id}/enrich/accept   a richer statement, and saving it (enrich.py)

Registered by the planner service's create_app, which hands over what the routes share with the rest.
"""

import asyncio
import logging
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from meta_harness import db
from meta_harness.planner import enrich, planning, plans, specs, store, suggestions
from meta_harness.planner.sweep import goal_tracker
from meta_harness.trackers.base import TrackerError
from meta_harness.trackers.own import SCHEME as OWN

log = logging.getLogger("meta_harness.planner")
# How long a read of the plan waits for an ended planning task's plan to be collected before it answers with
# what stands.
PLAN_WAIT_SECONDS = 2.0


class DraftIn(BaseModel):
    draft_id: str = Field(min_length=1, max_length=40)
    repo: str
    title: str = Field(max_length=200)
    body: str = Field(max_length=60000)
    labels: list[str] = []
    depends_on: list[str] = []


class ItemIn(BaseModel):
    ref: str = Field(min_length=1, max_length=300)
    decision: str = Field(min_length=1, max_length=20)
    reason: str = Field(default="", max_length=2000)
    depends_on: list[str] = []
    replaced_by: list[str] = []


class PlanIn(BaseModel):
    drafts: list[DraftIn] = Field(max_length=200)
    # The decisions on the issues the goal already had. Left out: those of the version before stay.
    items: list[ItemIn] | None = Field(default=None, max_length=500)


class ApproveIn(BaseModel):
    # Whether the plan's new tasks become issues in the goal's tracker (true), or are kept in the meta-harness
    # alone. Left out: the tracker, as before. A goal with no tracker keeps them either way.
    track: bool = True


class ReplanIn(BaseModel):
    note: str = Field(default="", max_length=4000)


def register(
    app: FastAPI,
    operator: Callable[..., str],
    load: Callable[[str], dict],
    events: Any,
    tracker: Any,
    tasks: Any,
    activate: Callable[[str, str], Any],
    issues_of: Callable[[dict], Any],
    plan_again: Callable[[str, str], Any],
    sizer: Any = None,
) -> None:
    """Adds the plan routes. activate(goal_id, who) makes a planning goal active and sweeps it. issues_of(goal)
    reads the issues the goal has that no task has started on, for its planning task to plan over.
    plan_again(goal_id, who) sends a planned goal that is under way back to planning."""
    enrich.register(app, operator, load, events)
    # The collectings a read started and did not wait out, kept so they are not dropped half done.
    pending: set[asyncio.Task] = set()

    def _collected(task: asyncio.Task) -> None:
        pending.discard(task)
        if not task.cancelled() and (err := task.exception()):
            log.error(
                "the plan a read asked for could not be collected: %s: %s. The next sweep collects it",
                type(err).__name__,
                err,
            )

    def planning_goal(goal_id: str) -> dict:
        goal = load(goal_id)
        if goal["status"] != "planning":
            raise HTTPException(status_code=409, detail=f"a {goal['status']} goal has no plan to change")
        return goal

    def shown(goal: dict) -> dict:
        with db.connect(autocommit=True) as conn:
            plan = plans.latest(conn, goal["id"])
            approval = plans.approval(conn, goal["id"])
            asked = plans.asked_at(conn, goal["id"])
        return {
            "goal_id": goal["id"],
            "status": goal["status"],
            # An approval is writing the plan's new tasks to the tracker now: `plan.created` grows as each
            # exists. And why the last approval stopped, when it did not finish.
            **approval,
            # The planning task still drafting, if one is: its page shows the run.
            "planning_task_id": goal.get("plan_task_id"),
            "approved_at": goal.get("plan_approved_at"),
            # When planning was last asked for: with the plan's created_at, how long the planner took.
            "planning_asked_at": asked.isoformat() if asked else None,
            "label": goal["label"] or planning.goal_label(goal),
            "plan": plan,
            # A fix the planner suggests for this plan, with what it changes; None when there is none.
            "suggestion": suggestions.current(goal["id"]),
        }

    @app.get("/v1/goals/{goal_id}/plan")
    async def read_plan(goal_id: str, who: str = Depends(operator)) -> dict:
        """The goal's plan as it stands. An ended planning task's drafts become the plan when it is first asked
        for, not only at the next sweep. Sizing a plan's tasks can take a minute, so the answer waits a moment
        for it and then says the planner is still working: the collecting goes on, and the page asks again."""
        goal = load(goal_id)
        if goal["status"] == "planning" and goal.get("plan_task_id"):
            collecting = asyncio.create_task(planning.collect(goal, tasks, events, sizer=sizer, issues_of=issues_of))
            pending.add(collecting)
            collecting.add_done_callback(_collected)
            await asyncio.wait({collecting}, timeout=PLAN_WAIT_SECONDS)
            # Read again whether or not it ended: another caller's collecting may have saved the plan meanwhile.
            goal = load(goal_id)
        return await asyncio.to_thread(shown, goal)

    @app.put("/v1/goals/{goal_id}/plan")
    def save_plan(goal_id: str, body: PlanIn, who: str = Depends(operator)) -> dict:
        goal = planning_goal(goal_id)
        if goal.get("plan_task_id"):
            raise HTTPException(
                status_code=409, detail="the planning task is still drafting: wait for it, or cancel it"
            )
        items = [i.model_dump() for i in body.items] if body.items is not None else None
        planning.edit(goal, [d.model_dump() for d in body.drafts], who, events, items)
        return shown(goal)

    @app.post("/v1/goals/{goal_id}/replan", status_code=202)
    async def replan(goal_id: str, body: ReplanIn | None = None, who: str = Depends(operator)) -> dict:
        """Start a new planning task for the goal, with a note. For a goal in planning, its plan is made again.
        For a planned goal that is under way, the goal goes back to planning first: the tasks already running
        go on, the issues no task has started on are planned again, and nothing new starts until the new plan
        is approved."""
        goal = load(goal_id)
        if goal["status"] in ("active", "stalled") and goal.get("planned"):
            goal = await plan_again(goal_id, who)
        elif goal["status"] != "planning":
            why = "it was never planned" if goal["status"] in ("active", "stalled") else f"it is {goal['status']}"
            raise HTTPException(status_code=409, detail=f"this goal cannot be planned again: {why}")
        if goal.get("plan_task_id"):
            raise HTTPException(status_code=409, detail="a planning task is already drafting this goal's plan")
        try:
            note = (body.note.strip() if body else "") or None
            await planning.start(goal, tasks, events, who, note=note, issues=await issues_of(goal))
        except (planning.PlanError, TrackerError) as err:
            raise HTTPException(status_code=502, detail=str(err)) from err
        return await asyncio.to_thread(shown, load(goal_id))

    @app.post("/v1/goals/{goal_id}/suggestion/accept")
    def accept_suggestion(goal_id: str, who: str = Depends(operator)) -> dict:
        """Make the planner's suggested fix the goal's next plan version, for the operator to approve."""
        goal = planning_goal(goal_id)
        if goal.get("plan_task_id"):
            raise HTTPException(
                status_code=409, detail="a planning task is drafting a new plan: wait for it, then look again"
            )
        try:
            suggestions.accept(goal, who, events)
        except suggestions.SuggestionError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        return shown(load(goal_id))

    @app.post("/v1/goals/{goal_id}/suggestion/dismiss")
    def dismiss_suggestion(goal_id: str, who: str = Depends(operator)) -> dict:
        """Leave the planner's suggested fix behind: the current plan stands."""
        goal = planning_goal(goal_id)
        try:
            suggestions.dismiss(goal)
        except suggestions.SuggestionError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        events.emit("plan_fix_dismissed", {"goal_id": goal_id}, actor=who, tags={"goal_id": goal_id})
        return shown(goal)

    @app.post("/v1/goals/{goal_id}/approve")
    async def approve(goal_id: str, body: ApproveIn | None = None, who: str = Depends(operator)) -> dict:
        goal = planning_goal(goal_id)
        track = body.track if body else True
        if goal.get("plan_task_id"):
            raise HTTPException(status_code=409, detail="the planning task is still drafting: wait for its plan")
        if not goal["label"]:
            # The plan's issues carry the goal's own label, so its sweep and the operator both find them.
            def label() -> None:
                with db.connect(autocommit=True) as conn:
                    store.update(conn, goal_id, label=planning.goal_label(goal))

            await asyncio.to_thread(label)
            goal = load(goal_id)
        writer = goal_tracker(tracker, goal)
        # Kept in the meta-harness: the new tasks go to its own tracker, which a tracked goal's tracker carries
        # beside its own. A goal with no tracker has only that one.
        kept = getattr(writer, "own", None)
        # In the tracker when asked, or when the goal's tracker is the only one there is to write to.
        tracked = getattr(writer, "scheme", "") != OWN and (track or kept is None)

        def claim() -> bool:
            with db.connect(autocommit=True) as conn:
                return plans.claim_approval(conn, goal_id)

        def end(error: str | None = None) -> None:
            with db.connect(autocommit=True) as conn:
                plans.end_approval(conn, goal_id, error)

        def keep() -> None:
            with db.connect(autocommit=True) as conn:
                plans.keep_approval(conn, goal_id)

        async def alive() -> None:
            # Renews the claim while the approval runs: one of a large plan can outlast APPROVAL_CLAIM_MINUTES.
            while True:
                await asyncio.sleep(plans.APPROVAL_REFRESH_SECONDS)
                try:
                    await asyncio.to_thread(keep)
                except Exception as err:  # A missed renewal is retried at the next; the claim still stands.
                    log.warning("could not renew the approval claim on goal %s: %s", goal_id, err)

        # One approval at a time. It writes each new task to the tracker, which outlasts a browser's wait, and
        # an operator who saw no answer pressed again: each press created every issue once more.
        if not await asyncio.to_thread(claim):
            raise HTTPException(
                status_code=409,
                detail=(
                    "this plan is being approved already: its tasks are being created now, and the goal's page "
                    "shows each as it appears. Nothing more is needed"
                ),
            )
        renewing = asyncio.create_task(alive())
        try:
            await planning.approve(
                goal,
                writer if tracked or kept is None else kept,
                events,
                who,
                tracked,
                originals=writer,
                spec=await asyncio.to_thread(specs.of, goal_id),
            )

            def approved() -> None:
                with db.connect(autocommit=True) as conn:
                    plans.set_approved(conn, goal_id)

            await asyncio.to_thread(approved)
            await activate(goal_id, who)
        except planning.PlanError as err:
            await asyncio.to_thread(end, str(err))
            code = 502 if str(err).startswith(planning.REFUSED) else 409
            raise HTTPException(status_code=code, detail=str(err)) from err
        except BaseException as err:
            # Whatever stopped it, the claim is let go and the reason kept: the operator whose request timed
            # out reads it on the goal's page, and approving again carries on from the tasks already created.
            await asyncio.shield(
                asyncio.to_thread(end, f"the approval stopped before it finished: {err or type(err).__name__}")
            )
            raise
        finally:
            renewing.cancel()
        await asyncio.to_thread(end)
        return await asyncio.to_thread(shown, load(goal_id))
