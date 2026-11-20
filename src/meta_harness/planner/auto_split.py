"""The planner splits an issue it held as too large itself, instead of waiting for an operator.

After each sweep of an active goal, each issue the sweep held for its size (and that no one has split, run
anyway, or made a task of) is split the way an operator's Split would: one model call drafts the parts, each a
complete issue (splits.draft), and split_issue creates them, labeled, each with a `Split from` line, the
original told where its work went. Every try is a remedy in the supervisor's ledger, keyed by the issue's ref,
with why, what was done and what comes next. A try that fails counts too, so the planner stops at the task
policy's `remedies.auto_split` tries (default 1, 0 turns it off) and the held issue is the operator's, with its
Split and Run anyway buttons.
"""

import asyncio
import logging
from typing import Any

from meta_harness.config.kinds import remedy_tries
from meta_harness.planner import splits, store
from meta_harness.planner.split_issue import SplitRefused, split_issue
from meta_harness.planner.sweep import goal_tracker
from meta_harness.run_service import routing
from meta_harness.supervisor import ledger
from meta_harness.trackers.base import TrackerError
from meta_harness import db

log = logging.getLogger("meta_harness.planner")
REMEDY = "auto_split"


def _held_large(goal_id: str) -> list[dict]:
    with db.connect(autocommit=True) as conn:
        links = store.links(conn, goal_id)
    return [
        link
        for link in links
        if link["state"] == "open"
        and str(link.get("held_reason") or "").startswith("sized large")
        and link.get("split_into") is None
        and not link.get("run_anyway")
        and not link.get("task_id")
        and not link.get("dropped")
    ]


async def run(goal: dict, tracker: Any, events: Any) -> list[str]:
    """Split each issue of the active goal held for its size. Returns the refs split."""
    if goal["status"] != "active":
        return []
    most = remedy_tries(routing.task_policy(), REMEDY)
    done = []
    for link in await asyncio.to_thread(_held_large, goal["id"]):
        tried = await asyncio.to_thread(ledger.tries_for_issue, link["ref"], REMEDY)
        if tried >= most:
            continue
        record = {
            "ref": link["ref"],
            "goal_id": goal["id"],
            "situation": "held_large",
            "remedy": REMEDY,
            "try": tried + 1,
            "of": most,
            "because": "the issue is too large for one pull request",
        }
        try:
            issue = await goal_tracker(tracker, goal).read(link["ref"])
            drafts, model, cost = await splits.draft(issue, list((link.get("sizing") or {}).get("split") or []))
            events.emit(
                "split_drafted",
                {"goal_id": goal["id"], "ref": link["ref"], "parts": len(drafts), "model": model, "usd_cost": cost},
                actor="planner",
                tags={"goal_id": goal["id"]},
            )
            created = await split_issue(
                goal, link, [(d["title"], d["body"]) for d in drafts], tracker, events, "planner"
            )
        except (splits.SplitError, SplitRefused, TrackerError) as err:
            log.warning(
                "goal %s: could not split %s itself: %s. It waits for an operator", goal["id"], link["ref"], err
            )
            await _record(
                events,
                {
                    **record,
                    "outcome": "failed",
                    "did": "could not split it",
                    "next": "split it, or run it as one, from the goal's page"
                    if tried + 1 >= most
                    else "I try again later",
                    "error": str(err)[:500],
                },
            )
            continue
        names = ", ".join(splits.short(r) for r in created)
        await _record(
            events,
            {
                **record,
                "outcome": "done",
                "did": f"split it into {len(created)} smaller issues: {names}",
                "next": "they run in its place, and it closes once they are all done",
                "error": None,
            },
        )
        done.append(link["ref"])
    return done


async def _record(events: Any, record: dict) -> None:
    await asyncio.to_thread(ledger.write, record)
    payload = {k: record[k] for k in ("situation", "remedy", "try", "outcome", "because", "did", "next")}
    payload |= {"of": record["of"] or 0, "ref": record["ref"]}
    if record.get("error"):
        payload["error"] = record["error"]
    events.emit("remedy_applied", payload, actor="planner", tags={"goal_id": record["goal_id"]})
