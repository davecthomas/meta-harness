"""Telling an issue's tracker why the meta-harness has not started the issue.

A person hands an issue over where they work: they give it the goal's label, or assign it to the meta-harness's
account. When the issue is then held or refused, nothing in the tracker says so, and the person waits on work
that is not coming. So each sweep comments on each of the goal's open issues that has no task and a reason for
it (`held_reason`, else `refused_reason`), with the reason and what to do. It comments once for each reason: the
reason last given is kept (`issue_link.replied`), so a sweep that finds the same reason writes nothing, and a
comment that failed is tried again by the next sweep. Every tracker is told, GitHub too: a held issue shows
nothing there either.
"""

import asyncio

from meta_harness import db
from meta_harness.planner import store
from meta_harness.run_service.provenance import COMMENT, Provenance, stamp
from meta_harness.trackers.base import Tracker, TrackerError


def reply_for(reason: str) -> str:
    """The comment that gives the reason, and what its reader can do."""
    return (
        f"The meta-harness has not started work on this issue: {reason.rstrip('.')}.\n\n"
        "It reads the issue again every minute, so a change to the issue is picked up by itself. An operator "
        "can also act on it from its goal's page in the meta-harness console."
    )


def reason_of(link: dict) -> str | None:
    """Why the goal's issue has no task, when it is open, still the goal's and has a reason; else None."""
    if link.get("task_id") or link.get("dropped") or link.get("state") != "open":
        return None
    return link.get("held_reason") or link.get("refused_reason") or None


async def write(goal: dict, tracker: Tracker, errors: list[str]) -> list[str]:
    """Comment on each of the goal's issues that is not started for a reason its tracker has not been given.
    Returns the refs written to; a comment that fails is added to errors and is tried again next sweep."""
    written = []
    for link in await asyncio.to_thread(_links, goal["id"]):
        reason = reason_of(link)
        if not reason or reason == link.get("replied"):
            continue
        try:
            await tracker.comment(link["ref"], stamp(reply_for(reason), Provenance(COMMENT, goal=goal["id"])))
        except TrackerError as err:
            errors.append(f"{link['ref']}: could not tell its tracker why it is not started: {err}")
            continue
        await asyncio.to_thread(_save, link["ref"], reason)
        written.append(link["ref"])
    return written


def _links(goal_id: str) -> list[dict]:
    with db.connect(autocommit=True) as conn:
        return store.links(conn, goal_id)


def _save(ref: str, reason: str) -> None:
    with db.connect(autocommit=True) as conn:
        store.set_link_replied(conn, ref, reason)
