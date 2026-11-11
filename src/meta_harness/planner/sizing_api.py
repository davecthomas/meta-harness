"""The held-issue routes of the planner's API: an operator's answer to an issue the sweep held (sizing.py).

    POST /v1/goals/{id}/issues/run-anyway   {ref}: make the held issue a task whatever its size and gaps
    POST /v1/goals/{id}/issues/split        {ref, issues: [{title, body}]}: create the smaller issues

A split creates each issue in the held issue's repo with the goal's label, so
the goal's next sweep takes them in; a goal with no label holds them by ref
instead. Each one's body names its paths on a `Paths:` line. The held issue
stays held, saying what it was split into, until it is closed: the planner
never closes an issue a person wrote. The split is recorded on its link
(split_into), so later sweeps keep that reason, and an issue split once is
not split again (409); Run anyway still makes it a task. While a split is
creating its issues (split_into is empty), Run anyway is refused (409) until
the split ends.

Registered by the planner service's create_app, which hands over what the routes share with the rest.
"""

import asyncio
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from meta_harness import db
from meta_harness.github_app import GitHubAppError
from meta_harness.planner import sizing, store
from meta_harness.run_service.code_host import issue_paths

MAX_SPLIT_ISSUES = 20


class HeldIn(BaseModel):
    ref: str = Field(min_length=1, max_length=300)


class SplitIssueIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=60000)


class SplitIn(BaseModel):
    ref: str = Field(min_length=1, max_length=300)
    issues: list[SplitIssueIn] = Field(min_length=1, max_length=MAX_SPLIT_ISSUES)


def register(
    app: FastAPI,
    operator: Callable[..., str],
    load: Callable[[str], dict],
    events: Any,
    tracker: Any,
    swept: Callable[[dict], Any],
) -> None:
    """Adds the held-issue routes. swept(goal) sweeps an active goal at once, so its answer takes effect now."""

    def held(goal_id: str, ref: str) -> tuple[dict, dict]:
        """The open goal and its held link for ref. 404 when the goal does not hold it, 409 when it is not held."""
        goal = load(goal_id)
        if goal["status"] in ("met", "canceled"):
            raise HTTPException(status_code=409, detail=f"the goal is {goal['status']}: reopen it first")
        with db.connect(autocommit=True) as conn:
            link = store.link(conn, ref)
        if link is None or link["goal_id"] != goal_id:
            raise HTTPException(status_code=404, detail=f"the goal does not hold {ref}")
        if not link["held_reason"] or link["task_id"]:
            raise HTTPException(status_code=409, detail=f"{ref} is not held")
        return goal, link

    def sweep_if_active(goal: dict) -> None:
        if goal["status"] == "active":
            asyncio.create_task(swept(goal))

    @app.post("/v1/goals/{goal_id}/issues/run-anyway")
    async def run_anyway(goal_id: str, body: HeldIn, who: str = Depends(operator)) -> dict:
        """Make a held issue a task at the goal's next sweep, which an active goal runs at once."""
        goal, _ = await asyncio.to_thread(held, goal_id, body.ref)

        def release() -> dict:
            with db.connect(autocommit=True) as conn:
                return store.run_anyway(conn, goal_id, body.ref)

        try:
            was = await asyncio.to_thread(release)
        except store.NotFound as err:
            raise HTTPException(status_code=404, detail=f"the goal does not hold {body.ref}") from err
        except store.GoalError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
        events.emit(
            "issue_released",
            {"ref": body.ref, "goal_id": goal_id, "held_reason": was["held_reason"]},
            actor=who,
            tags={"goal_id": goal_id},
        )
        sweep_if_active(goal)
        return {"goal_id": goal_id, "ref": body.ref, "run_anyway": True}

    @app.post("/v1/goals/{goal_id}/issues/split", status_code=201)
    async def split(goal_id: str, body: SplitIn, who: str = Depends(operator)) -> dict:
        """Create the smaller issues a held one splits into, each with the goal's label and a `Paths:` line."""
        goal, link = await asyncio.to_thread(held, goal_id, body.ref)
        pathless = [issue.title for issue in body.issues if not issue_paths(issue.body)]
        if pathless:
            raise HTTPException(
                status_code=422,
                detail=f"each issue names its paths on a Paths: line; these do not: {'; '.join(pathless)}",
            )
        repo, label = link["repo"], goal["label"]

        def claim() -> bool:
            with db.connect(autocommit=True) as conn:
                return store.claim_split(conn, goal_id, body.ref)

        def record(created: list[str]) -> list[str]:
            with db.connect(autocommit=True) as conn:
                # A goal with no label takes the new issues by ref, as an operator's pick.
                added = [] if label or not created else store.add_refs(conn, goal_id, [(ref, repo) for ref in created])
                store.record_split(conn, body.ref, created, sizing.split_reason(created) if created else None)
                return added

        if link.get("split_into") is not None or not await asyncio.to_thread(claim):
            done = ", ".join(link.get("split_into") or [])
            raise HTTPException(
                status_code=409, detail=f"{body.ref} was split already" + (f" into {done}" if done else "")
            )
        created: list[str] = []
        try:
            if label:
                await tracker.ensure_label(repo, label)
            for issue in body.issues:
                made = await tracker.create_issue(repo, issue.title.strip(), issue.body, [label] if label else [])
                created.append(made.ref)
        except GitHubAppError as err:
            # What was created is recorded as the split, so a retry never creates it twice; none: it may be split again.
            await asyncio.to_thread(record, created)
            done = f"; created before it refused: {', '.join(created)}" if created else ""
            raise HTTPException(status_code=502, detail=f"GitHub refused an issue: {err}{done}") from err
        except Exception:
            await asyncio.to_thread(record, created)
            raise

        added = await asyncio.to_thread(record, created)
        for ref in added:
            events.emit("issue_added", {"ref": ref, "goal_id": goal_id}, actor=who, tags={"goal_id": goal_id})
        events.emit(
            "issue_split",
            {"ref": body.ref, "goal_id": goal_id, "issues": created},
            actor=who,
            tags={"goal_id": goal_id},
        )
        sweep_if_active(goal)
        return {"goal_id": goal_id, "ref": body.ref, "created": created}
