"""Closing the issues a canceled goal's plan created.

A goal planned from a specification creates its tasks as issues in its tracker when its plan is approved.
Canceling the goal ended its tasks and left those issues open, with the goal's label, for nobody. With
`close_issues`, a cancel also closes them, as work that will not be done: "Won't Do" in Jira, closed as not
planned on GitHub. Each gets a comment that says why first.

Only issues the goal itself created are closed: those its plans created, by the record each plan keeps of
them; the parts it split one of its issues into; and any other that says so itself, in the provenance line
the planner signs each issue with. That last covers an issue an approval created and failed to record. An issue that existed before the goal is never closed,
nor one whose task has a pull request open, which the cancel leaves for the operator. One the tracker will
not close stays open and is named in the answer, with the tracker's reason.

A goal that has already ended can have them closed too:

    GET  /v1/goals/{id}/left-open        the open issues the goal created, for its page to list
    POST /v1/goals/{id}/close-left-open  close them as not planned; 409 while the goal is still open

A goal canceled before a cancel could close anything, or met with duplicates left over, would otherwise keep
its issues open in the tracker with no way from the console to close them.
"""

import asyncio
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException

from meta_harness import db, outbound
from meta_harness.planner import plans, store
from meta_harness.run_service.provenance import COMMENT, ISSUE, Provenance
from meta_harness.run_service.provenance import parse as parse_provenance
from meta_harness.trackers.base import Tracker, TrackerError


def reason(who: str) -> str:
    return f"Closed as not planned: the goal this issue was created for was canceled by {who}."


# A goal whose work is over: its issues are no longer waiting for a task of its own.
ENDED = ("canceled", "met")
# A task parked with its pull request open, which a cancel leaves for the operator.
PARKED = ["ready", "review_unresolved"]


def _open_links(goal_id: str) -> tuple[list[dict], set[str]]:
    """The goal's open issues, and the refs it is on record as having created: by one of its plans, or as a
    part of an issue it split."""
    with db.connect(autocommit=True) as conn:
        links = store.links(conn, goal_id)
        created = plans.created_refs(conn, goal_id)
        for link in links:
            created.update(link.get("split_into") or [])
        return [link for link in links if link["state"] == "open" and not link.get("dropped")], created


def signed_by(body: str, goal_id: str) -> bool:
    """Whether an issue's text says the goal's plan created it: the provenance line the planner signs with."""
    return any(p.get("kind") == ISSUE and p.get("goal") == goal_id for p in parse_provenance(body or ""))


async def open_created(goal: dict, tracker: Tracker) -> list[dict]:
    """The goal's open issues that it created. One that is not on record is read from the tracker, and counts
    when its own text says this goal's plan made it. An issue that cannot be read is left out: nothing is
    closed on a guess."""
    links, created = await asyncio.to_thread(_open_links, goal["id"])
    found = []
    for link in links:
        if link["ref"] in created:
            found.append(link)
            continue
        try:
            issue = await tracker.read(link["ref"])
        except (TrackerError, ValueError):
            continue
        if signed_by(issue.body, goal["id"]):
            found.append(link)
    return found


def _closed(ref: str) -> None:
    with db.connect(autocommit=True) as conn:
        store.set_link_state(conn, ref, "closed")


async def close_created(goal: dict, tracker: Tracker, who: str, left: list[str]) -> dict[str, list[str]]:
    """Close each open issue the goal's plans created. left: the ids of the tasks the cancel left with a pull
    request open, whose issues stay open too. Returns the refs closed, and for each one that could not be
    closed, the ref with why."""
    done: dict[str, list[str]] = {"closed": [], "errors": []}
    close = getattr(tracker, "close_unplanned", None)
    for link in await open_created(goal, tracker):
        ref = link["ref"]
        if link.get("task_id") in left:
            continue
        if close is None:
            done["errors"].append(f"{ref}: its tracker has no way to close an issue as not planned. Close it there")
            continue
        try:
            signed = Provenance(COMMENT, goal=goal["id"])
            await tracker.comment(
                ref, await outbound.for_tracker(reason(who), ref, outbound.COMMENT, outbound.FIXED, provenance=signed)
            )
            await close(ref)
        except (TrackerError, ValueError) as err:
            done["errors"].append(f"{ref}: {err}")
            continue
        await asyncio.to_thread(_closed, ref)
        done["closed"].append(ref)
    return done


def register(app: FastAPI, operator: Callable[..., str], load: Callable[[str], dict], tracker: Any, tasks: Any) -> None:
    """Adds the routes that list and close what an ended goal left open."""
    from meta_harness.planner.sweep import goal_tracker
    from meta_harness.tasks.client import TaskApiError

    @app.get("/v1/goals/{goal_id}/left-open")
    async def left_open(goal_id: str, who: str = Depends(operator)) -> dict:
        """The open issues the goal created, each with its title: what a cancel with `close_issues`, or
        close-left-open, would close. 404 for an unknown goal."""
        goal = await asyncio.to_thread(load, goal_id)
        found = await open_created(goal, goal_tracker(tracker, goal))
        return {"issues": [{"ref": link["ref"], "title": link.get("title") or link["ref"]} for link in found]}

    @app.post("/v1/goals/{goal_id}/close-left-open")
    async def close_left_open(goal_id: str, who: str = Depends(operator)) -> dict:
        """Close, as not planned, each open issue the ended goal created. Answers the refs `closed` and the
        `errors`, as a cancel with `close_issues` does. 409 for a goal that is still open: cancel it first,
        since its issues are its work until then."""
        goal = await asyncio.to_thread(load, goal_id)
        if goal["status"] not in ENDED:
            raise HTTPException(
                status_code=409,
                detail=f"the goal is {goal['status']}, so its open issues are still its work. Cancel the goal "
                "to close them, with the choice to close its issues turned on",
            )
        try:
            parked = await asyncio.to_thread(tasks.list, PARKED, goal_id, 1000)
        except TaskApiError as err:
            raise HTTPException(
                status_code=502, detail=f"the goal's tasks could not be read ({err}), so nothing was closed. Try again"
            ) from err
        left = [task["id"] for task in parked if task.get("pr_url")]
        return await close_created(goal, goal_tracker(tracker, goal), who, left)
