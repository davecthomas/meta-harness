"""One action that gets a goal's stuck tasks moving: each takes the step recommended for it.

    GET  /v1/goals/{id}/unstick   what the action would do, task by task, with nothing done
    POST /v1/goals/{id}/unstick   do it, and answer with what was done and what was left

When many of a goal's tasks wait on a person at once, the goal's page made the operator open each one and
press Retry, Merge or Start over. The supervisor already ranks the moves for every task it hands to a person
(supervisor/moves.py), the first being the one most likely to get the task merged. This takes that first move
for each such task, as the operator pressing it would, through the Task API under the operator's name.

A task is left, with why, when its first move is one only a person makes (opening a pull request for an
approval, opening the goal), when it is held for its goal's budget, or when it has no move. Ending a task
and merging one with its findings unfixed are never taken here: those are a person's decision, task by task.

Registered by the planner service's create_app.
"""

import asyncio
from typing import Any, Callable

from fastapi import Depends, FastAPI

from meta_harness import db
from meta_harness.supervisor import standing
from meta_harness.tasks.client import TaskApiError

# The most of a goal's tasks one look reads: the Task API's page limit.
TASKS_PAGE = 1000
# The moves this action takes, and what each does in a few words. Any other first move is the operator's own.
STEPS = {
    "merge": "merges its pull request",
    "resolve_and_merge": "resolves its conflict with the base, then merges",
    "retry_stronger": "retries it with a stronger model",
    "retry": "retries it",
    "start_over": "starts it over on the current base",
}
RAISE = "raise"
END_HOLD = "end_hold"
STEPS_FOR_HOLDS = {RAISE: "raises its spend limit for its next run", END_HOLD: "ends its wait for budget"}
# Why a task with one of these first moves is left for the operator.
LEFT_FOR = {
    "open_pull_request": "its pull request needs something only a person gives on the code host",
    "open_goal": "its goal is not running: resume it or approve its plan",
    "merge_as_is": "merging it with its review's findings unfixed is yours to decide",
    "cancel": "canceling it is yours to decide",
}


def step_for(task: dict) -> tuple[str, str]:
    """(step, "") for a stuck task this action moves, or ("", why) for one it leaves. A task that is not an
    operator's is not stuck: ("", "")."""
    stood = task.get("standing") or {}
    if stood.get("owner") != "operator":
        return "", ""
    request = task.get("budget_request")
    if task.get("waiting_on") == "budget":
        if not request or request.get("for") != "task":
            return "", "it waits on its goal's budget: raise the goal's budget on its page"
        return (END_HOLD, "") if request.get("over_ceiling") else (RAISE, "")
    for move in stood.get("moves") or []:
        if move in STEPS:
            return move, ""
        return "", LEFT_FOR.get(move, f"its next step ({move}) is yours to take on its page")
    return "", stood.get("ask") or "it has no step that can be taken for you"


def plan(tasks: list[dict]) -> dict:
    """What the action would do for these tasks: the ones it moves, each with its step, and the ones it
    leaves, each with why."""
    moves, left = [], []
    for task in tasks:
        step, why = step_for(task)
        shown = {"task_id": task["id"], "name": task.get("name") or task.get("title") or task["id"]}
        if step:
            moves.append(shown | {"step": step, "does": (STEPS | STEPS_FOR_HOLDS)[step]})
        elif why:
            left.append(shown | {"why": why})
    return {"moves": moves, "left": left}


def take(tasks: Any, task: dict, step: str, who: str) -> None:
    """Take one task's step through the Task API, as the operator pressing its button would."""
    task_id = task["id"]
    if step in ("merge", "resolve_and_merge"):
        tasks.merge(task_id, actor=who)
    elif step == "retry":
        tasks.retry(task_id, actor=who)
    elif step == "retry_stronger":
        tasks.retry(task_id, actor=who, complexity="high")
    elif step == "start_over":
        tasks.restart(task_id, actor=who)
    elif step == RAISE:
        tasks.budget(task_id, "raise", task["budget_request"]["to_usd"], actor=who)
    elif step == END_HOLD:
        tasks.budget(task_id, "end", None, actor=who)
    else:
        raise ValueError(f"no step {step!r}")


def register(app: FastAPI, operator: Callable[..., str], load: Callable[[str], dict], tasks: Any) -> None:
    """Adds the two routes. load(goal_id) reads the goal or answers 404."""

    def stuck(goal_id: str) -> list[dict]:
        found = tasks.list(None, goal_id, TASKS_PAGE)
        with db.connect(autocommit=True) as conn:
            stood = standing.for_tasks(conn, [task["id"] for task in found])
        return [task | {"standing": stood.get(task["id"])} for task in found]

    @app.get("/v1/goals/{goal_id}/unstick")
    async def would_unstick(goal_id: str, who: str = Depends(operator)) -> dict:
        """What getting the goal's stuck tasks moving would do, with nothing done."""
        load(goal_id)
        return plan(await asyncio.to_thread(stuck, goal_id))

    @app.post("/v1/goals/{goal_id}/unstick")
    async def unstick(goal_id: str, who: str = Depends(operator)) -> dict:
        """Take the recommended step for each of the goal's tasks that waits on a person. Answers with each
        task it moved and its step, each it could not move and the Task API's reason, and each it left."""
        load(goal_id)
        found = await asyncio.to_thread(stuck, goal_id)
        by_id = {task["id"]: task for task in found}
        planned = plan(found)
        done, failed = [], []
        for move in planned["moves"]:
            try:
                await asyncio.to_thread(take, tasks, by_id[move["task_id"]], move["step"], who)
                done.append(move)
            except TaskApiError as err:
                # One task's refusal does not stop the rest: it is said, with what the Task API answered.
                failed.append(move | {"why": f"the Task API refused it: {err.detail}"})
        return {"done": done, "failed": failed, "left": planned["left"]}
