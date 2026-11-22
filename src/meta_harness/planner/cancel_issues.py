"""Closing the issues a canceled goal's plan created.

A goal planned from a specification creates its tasks as issues in its tracker when its plan is approved.
Canceling the goal ended its tasks and left those issues open, with the goal's label, for nobody. With
`close_issues`, a cancel also closes them, as work that will not be done: "Won't Do" in Jira, closed as not
planned on GitHub. Each gets a comment that says why first.

Only issues the goal's own plans created are closed, by the record each plan keeps of them. An issue that
existed before the goal is never closed, nor one whose task has a pull request open, which the cancel leaves
for the operator. One the tracker will not close stays open and is named in the answer, with the tracker's
reason.
"""

import asyncio

from meta_harness import db, outbound
from meta_harness.planner import plans, store
from meta_harness.run_service.provenance import COMMENT, Provenance
from meta_harness.trackers.base import Tracker, TrackerError


def reason(who: str) -> str:
    return f"Closed as not planned: the goal this issue was created for was canceled by {who}."


def _open_created(goal_id: str) -> list[dict]:
    """The goal's open issues that one of its plans created."""
    with db.connect(autocommit=True) as conn:
        created = plans.created_refs(conn, goal_id)
        return [
            link
            for link in store.links(conn, goal_id)
            if link["ref"] in created and link["state"] == "open" and not link.get("dropped")
        ]


def _closed(ref: str) -> None:
    with db.connect(autocommit=True) as conn:
        store.set_link_state(conn, ref, "closed")


async def close_created(goal: dict, tracker: Tracker, who: str, left: list[str]) -> dict[str, list[str]]:
    """Close each open issue the goal's plans created. left: the ids of the tasks the cancel left with a pull
    request open, whose issues stay open too. Returns the refs closed, and for each one that could not be
    closed, the ref with why."""
    done: dict[str, list[str]] = {"closed": [], "errors": []}
    close = getattr(tracker, "close_unplanned", None)
    for link in await asyncio.to_thread(_open_created, goal["id"]):
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
