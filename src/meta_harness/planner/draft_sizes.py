"""Sizing a plan's new tasks before the plan is shown, so each is one that a single run can finish.

An issue the sweep takes in is sized first (sizing.py): one too large for a pull request, or without the checks,
paths or outcome a run needs, is held. A plan's own new tasks used to skip that: an approved plan was the
review, and a task its planner had written too large ran until it was out of turns, then had to be split. So
the planner's drafts now meet the same sizer when its planning task ends. One that comes back large, or
lacking what a run needs, makes the plan not approvable and says why, and the planner is sent back to resize
them, a bounded number of times (the planning configuration's `resize_rounds`), before an operator sees the
plan. A task that lacks its checks, paths or outcome is sent back only in a goal that is strict about its
issues, the rule the sweep keeps for an issue. What it could not get right in those rounds stays on the plan as the reason it cannot be approved.
"""

import logging
import re
from typing import Any

from meta_harness.events.client import stable_event_id
from meta_harness.planner import copies, sizing
from meta_harness.planner.tracker import Issue, acceptance_checks, issue_paths

log = logging.getLogger("meta_harness.planner")

# How many times the planner is sent back to resize a plan's tasks, unless the planning configuration says.
DEFAULT_ROUNDS = 2
# A goal's tasks as last sized, by a hash of each one's text: a task the planner kept as it was through a
# resize round is not sized again. Kept in the planner's memory, so a restart sizes a plan's tasks once more.
_sized: dict[str, dict[str, sizing.Sized]] = {}
MAX_GOALS_REMEMBERED = 200
# How a planning task that resizes is told apart: the end of its external ref, with its round.
_ROUND = re.compile(r":resize(\d+)$")
LACKS = {
    "acceptance_checks": "acceptance checks",
    "paths": "a Paths: line",
    "testable_outcome": "an outcome a test can prove",
}


def round_of(task: dict) -> int:
    """Which resize round the planning task was: 0 for one that planned afresh."""
    found = _ROUND.search(str(task.get("external_ref") or ""))
    return int(found.group(1)) if found else 0


def resize_ref(goal_id: str, version: int, round_no: int) -> str:
    """The external ref of the planning task that resizes the goal's plan, for that round."""
    return f"plan:{goal_id}:{version}:resize{round_no}"


async def findings(goal: dict, drafts: list[dict[str, Any]], sizer: sizing.Sizer | None, events: Any) -> list[dict]:
    """Each draft the sizer would not let run as one task: too large, or lacking what a run needs. Each
    sizing is recorded as issue_sized, as an issue's is, so its cost counts as planning. A draft that cannot
    be sized is let through: the sweep's own look is not this one's to replace."""
    if sizer is None:
        return []
    found = []
    known = _sized.pop(goal["id"], {})
    kept: dict[str, sizing.Sized] = {}
    # Put back last, so the goals sized longest ago are the ones forgotten.
    _sized[goal["id"]] = kept
    while len(_sized) > MAX_GOALS_REMEMBERED:
        del _sized[next(iter(_sized))]
    for draft in drafts:
        body = draft.get("body") or ""
        issue = Issue(
            ref=f"draft:{goal['id']}:{draft['draft_id']}",
            repo=draft.get("repo") or goal["repos"][0],
            number=0,
            title=draft.get("title") or draft["draft_id"],
            body=body,
            labels=list(draft.get("labels") or []),
            state="open",
            checks=acceptance_checks(body),
            paths=issue_paths(body),
        )
        text = copies.text_hash(issue.title, body)
        sized = known.get(text)
        fresh = sized is None
        if fresh:
            try:
                sized = await sizer(issue)
            except Exception as err:  # noqa: BLE001 - a task the sizer fails on is let through, as the sweep does
                log.warning("goal %s: the plan's task %s could not be sized: %s", goal["id"], draft["draft_id"], err)
                continue
        if sized is None:
            continue
        kept[text] = sized
        if fresh:
            events.emit(
                "issue_sized",
                {
                    "ref": issue.ref,
                    "goal_id": goal["id"],
                    "size": sized.size,
                    "reasons": sized.reasons,
                    "missing": sized.missing,
                    "split": sized.split,
                    "model": sized.model,
                    "usd_cost": sized.usd_cost,
                },
                actor="planner",
                tags={"goal_id": goal["id"]},
                event_id=stable_event_id("issue_sized", issue.ref, goal["id"], str(len(body)), sized.size),
            )
        # What a task lacks stops it only in a goal that is strict about its issues, as it does an issue the
        # sweep takes in. Too large stops it in any goal.
        missing = sized.missing if goal.get("strict_issues") else []
        if sized.size == "large" or missing:
            found.append(
                {
                    "draft_id": draft["draft_id"],
                    "title": issue.title,
                    "large": sized.size == "large",
                    "reasons": sized.reasons,
                    "missing": missing,
                    "split": sized.split,
                }
            )
    return found


def _said(finding: dict) -> str:
    parts = []
    if finding["large"]:
        why = f": {'; '.join(finding['reasons'])}" if finding["reasons"] else ""
        parts.append(f"is too large for one pull request{why}")
    if finding["missing"]:
        parts.append("lacks " + " and ".join(LACKS.get(m, m) for m in finding["missing"]))
    return " and ".join(parts)


def reasons(found: list[dict]) -> list[str]:
    """Why the plan cannot be approved, a line for each task that is not one run's work, with what to do."""
    lines = []
    for finding in found:
        fix = "Split it into smaller tasks" if finding["large"] else "Edit it to add what it lacks"
        lines.append(f"the task {finding['draft_id']} {_said(finding)}. {fix}, or ask for a new plan")
    return lines


def note(found: list[dict]) -> str:
    """What the planner is told when it is sent back: which of its tasks to resize, and how."""
    lines = [
        "Resize the plan below. Keep every task that is not named here as it is, with its draft_id.",
        "Each task named here is not one run's work as written:",
    ]
    for finding in found:
        line = f"- {finding['draft_id']} ({finding['title']}) {_said(finding)}."
        if finding["large"] and finding["split"]:
            line += f" It could split into: {'; '.join(finding['split'])}."
        if finding["large"]:
            line += " Replace it with smaller tasks, each one pull request, and fix what waits on it."
        lines.append(line)
    lines.append("Give every task its acceptance checks, its Paths: line and an outcome a test can prove.")
    return "\n".join(lines)
