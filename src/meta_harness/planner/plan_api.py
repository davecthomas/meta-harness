"""The plan routes of the planner's API: read a feature goal's plan, edit it, ask for a new one, approve it.

    GET  /v1/goals/{id}/plan      the newest plan version, and the planning task in flight if there is one
    PUT  /v1/goals/{id}/plan      save edited drafts as the next version
    POST /v1/goals/{id}/replan    start a new planning task, with a note
    POST /v1/goals/{id}/approve   create the plan's issues and make the goal active

Registered by the planner service's create_app, which hands over what the routes share with the rest.
"""

import asyncio
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from meta_harness import db
from meta_harness.planner import planning, plans, store


class DraftIn(BaseModel):
    draft_id: str = Field(min_length=1, max_length=40)
    repo: str
    title: str = Field(max_length=200)
    body: str = Field(max_length=60000)
    labels: list[str] = []
    depends_on: list[str] = []


class PlanIn(BaseModel):
    drafts: list[DraftIn] = Field(max_length=200)


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
) -> None:
    """Adds the plan routes. activate(goal_id, who) makes a planning goal active and sweeps it."""

    def planning_goal(goal_id: str) -> dict:
        goal = load(goal_id)
        if goal["status"] != "planning":
            raise HTTPException(status_code=409, detail=f"a {goal['status']} goal has no plan to change")
        return goal

    def shown(goal: dict) -> dict:
        with db.connect(autocommit=True) as conn:
            plan = plans.latest(conn, goal["id"])
        return {
            "goal_id": goal["id"],
            "status": goal["status"],
            # The planning task still drafting, if one is: its page shows the run.
            "planning_task_id": goal.get("plan_task_id"),
            "approved_at": goal.get("plan_approved_at"),
            "label": goal["label"] or planning.goal_label(goal),
            "plan": plan,
        }

    @app.get("/v1/goals/{goal_id}/plan")
    async def read_plan(goal_id: str, who: str = Depends(operator)) -> dict:
        goal = load(goal_id)
        # An ended planning task's drafts become the plan when it is first asked for, not only at the next sweep.
        if goal["status"] == "planning" and await planning.collect(goal, tasks, events):
            goal = load(goal_id)
        return await asyncio.to_thread(shown, goal)

    @app.put("/v1/goals/{goal_id}/plan")
    def save_plan(goal_id: str, body: PlanIn, who: str = Depends(operator)) -> dict:
        goal = planning_goal(goal_id)
        if goal.get("plan_task_id"):
            raise HTTPException(
                status_code=409, detail="the planning task is still drafting: wait for it, or cancel it"
            )
        planning.edit(goal, [d.model_dump() for d in body.drafts], who, events)
        return shown(goal)

    @app.post("/v1/goals/{goal_id}/replan", status_code=202)
    async def replan(goal_id: str, body: ReplanIn | None = None, who: str = Depends(operator)) -> dict:
        goal = planning_goal(goal_id)
        if goal.get("plan_task_id"):
            raise HTTPException(status_code=409, detail="a planning task is already drafting this goal's plan")
        try:
            await planning.start(goal, tasks, events, who, note=(body.note.strip() if body else "") or None)
        except planning.PlanError as err:
            raise HTTPException(status_code=502, detail=str(err)) from err
        return await asyncio.to_thread(shown, load(goal_id))

    @app.post("/v1/goals/{goal_id}/approve")
    async def approve(goal_id: str, who: str = Depends(operator)) -> dict:
        goal = planning_goal(goal_id)
        if goal.get("plan_task_id"):
            raise HTTPException(status_code=409, detail="the planning task is still drafting: wait for its plan")
        if not goal["label"]:
            # The plan's issues carry the goal's own label, so its sweep and the operator both find them.
            def label() -> None:
                with db.connect(autocommit=True) as conn:
                    store.update(conn, goal_id, label=planning.goal_label(goal))

            await asyncio.to_thread(label)
            goal = load(goal_id)
        try:
            await planning.approve(goal, tracker, events, who)
        except planning.PlanError as err:
            code = 502 if "GitHub refused" in str(err) else 409
            raise HTTPException(status_code=code, detail=str(err)) from err

        def approved() -> None:
            with db.connect(autocommit=True) as conn:
                plans.set_approved(conn, goal_id)

        await asyncio.to_thread(approved)
        await activate(goal_id, who)
        return await asyncio.to_thread(shown, load(goal_id))
