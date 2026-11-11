"""A goal's budget at work: the sweep's look at what the goal has spent, and the one-step resume.

    POST /v1/goals/{id}/resume-budget   raise a goal's budget so it can go on, by a worked-out amount or add_usd

Each sweep of a goal with a budget adds up what its tasks' runs spent (`attempt_finished`), saves it on the goal
for the API to show, records `goal_budget_reached` the first time the spend reaches the budget, and moves a
`block` goal to `budget_exhausted` at its blocking line. A `budget_exhausted` goal is swept too, so its pull
requests are still watched, and it goes back to `active` once its line is above what it has spent: an operator
raised the budget, switched it to `warn`, or cleared it.

Registered by the planner service's create_app, which hands over what the routes share with the rest.
"""

import asyncio
from decimal import Decimal
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from meta_harness import db
from meta_harness.events.client import stable_event_id
from meta_harness.planner import budget, store
from meta_harness.run_service.shared import DEFAULT_SPEND_LIMIT_USD

# The most of a goal's tasks one look reads: the Task API's page limit.
TASKS_PAGE = 1000
# A running task held for its goal's budget says so in its status reason (RunTask's _grant).
HELD_FOR_GOAL = "goal's budget"


class ResumeIn(BaseModel):
    # What to add to the goal's budget. Left out: the amount is worked out (budget.resume_to).
    add_usd: Decimal | None = Field(default=None, gt=0)


def figures(goal: dict, tasks: Any, events: Any) -> dict:
    """What the goal's runs have spent, its tasks, and the budget a resume would set with how it was worked out."""
    found = tasks.list(None, goal["id"], TASKS_PAGE)
    costs: dict[str, Decimal] = {}
    for task in found:
        page = events.read(0, 5000, task_id=task["id"], event_type="attempt_finished")
        costs[task["id"]] = sum((Decimal(str(e["payload"].get("spent_usd") or 0)) for e in page["events"]), Decimal(0))
    spent = sum(costs.values(), Decimal(0))
    with db.connect(autocommit=True) as conn:
        links = store.links(conn, goal["id"])
    worked = budget.worked_out(spent, found, costs, links, Decimal(DEFAULT_SPEND_LIMIT_USD))
    return {"spent": spent, "tasks": found, "resume": worked}


def _save(goal_id: str, spent: Decimal, resume_to: Decimal | None, reached: Decimal | None = None) -> None:
    with db.connect(autocommit=True) as conn:
        store.record_spend(conn, goal_id, spent, resume_to, reached)


def _move(goal_id: str, origin: str, status: str) -> tuple[dict, dict]:
    with db.connect(autocommit=True) as conn:
        return store.update(conn, goal_id, only_if=origin, status=status)


async def check(goal: dict, tasks: Any, events: Any) -> None:
    """A sweep's look at the goal's budget: save its spend, record the budget reached, and block or unblock it.
    Changes goal["status"] when it moves the goal."""
    if goal["status"] not in ("active", "budget_exhausted"):
        return
    line, spent = None, Decimal(0)
    if goal.get("max_usd") is not None:
        found = await asyncio.to_thread(figures, goal, tasks, events)
        spent, max_usd = found["spent"], Decimal(goal["max_usd"])
        line = budget.block_at(max_usd, goal["budget_mode"], goal["budget_overage_percent"])
        reached = None
        if spent >= max_usd and Decimal(goal["budget_reached_usd"] or -1) != max_usd:
            # Recorded before it is saved as recorded: a failed emit is tried again at the next sweep.
            events.emit(
                "goal_budget_reached",
                {
                    "goal_id": goal["id"],
                    "spent_usd": str(spent),
                    "max_usd": str(max_usd),
                    "mode": goal["budget_mode"],
                    "block_at": str(line) if line is not None else None,
                },
                actor="planner",
                tags={"goal_id": goal["id"]},
                event_id=stable_event_id("goal_budget_reached", goal["id"], str(max_usd)),
            )
            reached = max_usd
        resume_to = Decimal(found["resume"]["to"]) if spent >= max_usd else None
        await asyncio.to_thread(_save, goal["id"], spent, resume_to, reached)
    blocked = line is not None and spent >= line
    if blocked == (goal["status"] == "budget_exhausted"):
        return
    origin, status = ("active", "budget_exhausted") if blocked else ("budget_exhausted", "active")
    moved, changed = await asyncio.to_thread(_move, goal["id"], origin, status)
    if not changed:
        return
    events.emit(
        "goal_changed",
        {"goal_id": goal["id"], "changes": {"status": {"from": origin, "to": status}}},
        actor="planner",
        tags={"goal_id": goal["id"]},
    )
    goal["status"] = moved["status"]


def register(
    app: FastAPI,
    operator: Callable[..., str],
    load: Callable[[str], dict],
    change_with: Callable[..., tuple[dict, dict]],
    events: Any,
    tasks: Any,
) -> None:
    """Adds the resume route. change_with(goal_id, who, event_extra, **fields) changes a goal and records
    goal_changed, as the service's other routes do."""

    @app.post("/v1/goals/{goal_id}/resume-budget")
    async def resume_budget(goal_id: str, body: ResumeIn | None = None, who: str = Depends(operator)) -> dict:
        """Raise the budget of a goal that is over it or stopped by it, so the goal can go on: to a worked-out
        budget, or by add_usd. A budget_exhausted goal becomes active, and its running tasks held for the
        goal's budget are told to look again at once. The answer says the old budget, the new one and how it
        was worked out."""
        goal = await asyncio.to_thread(load, goal_id)
        if goal["max_usd"] is None:
            raise HTTPException(status_code=409, detail="the goal has no budget to raise")
        if goal["budget_mode"] == "warn":
            raise HTTPException(
                status_code=409, detail="the goal only warns at its budget, so it never stopped: nothing to resume"
            )
        if goal["status"] not in ("active", "budget_exhausted"):
            raise HTTPException(status_code=409, detail=f"the goal is {goal['status']}, not stopped by its budget")
        try:
            found = await asyncio.to_thread(figures, goal, tasks, events)
        except Exception as err:  # noqa: BLE001 - an unreachable task or event service is the caller's 502
            raise HTTPException(status_code=502, detail=f"could not read what the goal has spent: {err}") from err
        spent, old = found["spent"], Decimal(goal["max_usd"])
        mode, percent = goal["budget_mode"], goal["budget_overage_percent"]
        if budget.state(old, mode, percent, spent, goal["status"]) == "ok":
            raise HTTPException(
                status_code=409,
                detail=f"the goal has spent ${spent.quantize(budget.CENT)} of its ${old} budget: nothing to resume",
            )
        add = body.add_usd if body else None
        new = old + add if add is not None else Decimal(found["resume"]["to"])
        how = f"${old} plus the ${add} asked for" if add is not None else found["resume"]["how"]
        line = budget.block_at(new, mode, percent)
        if spent >= line:
            raise HTTPException(
                status_code=409,
                detail=f"a budget of ${new} stops the goal at ${line}, and it has spent ${spent.quantize(budget.CENT)}",
            )
        fields: dict[str, Any] = {"max_usd": new}
        if goal["status"] == "budget_exhausted":
            fields["status"] = "active"
        await asyncio.to_thread(lambda: change_with(goal_id, who, {"resumed": True}, **fields))
        await asyncio.to_thread(_save, goal_id, spent, None)
        told = []
        for task in found["tasks"]:
            held = task["status"] == "running" and task.get("waiting_on") == "budget"
            if not held or HELD_FOR_GOAL not in (task.get("status_reason") or ""):
                continue
            try:
                await asyncio.to_thread(tasks.budget, task["id"], "raise", actor=who)
            except Exception:  # noqa: BLE001 - the task looks again by itself within its next recheck
                continue
            told.append(task["id"])
        return {
            "goal": await asyncio.to_thread(load, goal_id),
            "from": str(old),
            "to": str(new),
            "spent_usd": str(spent.quantize(budget.CENT)),
            "how": how,
            "tasks_told": told,
        }
