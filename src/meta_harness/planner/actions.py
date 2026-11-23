"""What resolves something the planner asks or suggests: the action a page offers as a button.

The planner says what needs the operator in several places: a goal's update, the Overview's report, an answer
to a question. Each of those comes with the button that does it, and a button must match its sentence and
must work. So every place resolves its actions here, against the goal's records: the model may pick an action
from `ACTIONS`, and `actionable` checks the pick and turns one the records do not support into the nearest one
they do. A task's or an issue's own state overrides the pick (`STATE_ACTIONS`).

This module reads only what it is given: it calls nothing outside it.
"""

from meta_harness.planner import splits

# What resolves a need, so the page can offer it as a button: each need an update names comes with one.
ACTIONS = ("approve_plan", "accept_fix", "merge", "review", "open_task", "raise_budget", "open_issue")
# The actions a task's or an issue's own state decides, whatever the model wrote: a review no fix settled is
# settled on the spot (merge as is, try another fix), a failed task is retried, and an issue held for its size
# is split or run as one, and a plan that cannot be approved is planned again, never offered for approval.
STATE_ACTIONS = ("settle_review", "retry", "split_or_run", "fix_plan")


def _split_already(link: dict) -> bool:
    """An issue the plan or the operator already split waits for its parts: nothing is left to choose."""
    reason = str(link.get("held_reason") or "")
    return bool(link.get("split_into")) or reason.startswith(("split into", "the plan split it"))


def actionable(
    needs: list[dict],
    goal: dict,
    links: list[dict],
    tasks: list[dict],
    suggestion: dict | None = None,
    plan: dict | None = None,
) -> list[dict]:
    """Each need with what resolves it, checked against the goal's records: the action the page offers, and
    the task it acts on. Every need gets one. An action the records do not support (a merge of a task with no
    pull request ready, a plan approval on a goal not planning) becomes the nearest one they do, so the page
    never offers a button that cannot work, and never a need with no way to act on it.

    A plan is offered for approval only when it can be approved. One that cannot (plan: the goal's latest, with
    why in `invalid`) is offered to be planned again, so an Approve button never sits under a sentence asking
    for something else. While a planning task is writing a new version, a need about the plan is dropped: the
    version it speaks of is being replaced, and nothing about it is the operator's."""
    by_ref = {link["ref"]: link for link in links}
    by_id = {task["id"]: task for task in tasks}
    planning = goal["status"] == "planning"
    replanning = planning and bool(goal.get("plan_task_id"))
    of_plan = "fix_plan" if planning and (plan or {}).get("invalid") else "approve_plan"
    out = []
    for need in needs:
        link = by_ref.get(need.get("ref") or "")
        # A split issue's parts run in its place and it closes itself: nothing about it is the operator's.
        if link and splits.is_replaced(link) and not need.get("action") == "accept_fix":
            continue
        task = by_id.get((link or {}).get("task_id") or "")
        wanted = need.get("action") if need.get("action") in ACTIONS else ""
        pr = (task or {}).get("pr_url") or ""
        status = (task or {}).get("status")
        if wanted == "accept_fix" and suggestion:
            action = "accept_fix"
        elif task and pr and status == "review_unresolved":
            action = "settle_review"
        elif task and pr and status == "ready":
            action = "merge"
        elif task and status == "failed":
            action = "retry"
        elif not task and link and link.get("held_reason") and not _split_already(link):
            action = "split_or_run"
        elif wanted == "approve_plan" and planning:
            action = of_plan
        elif wanted == "merge" and task and pr and task.get("status") in ("ready", "review_unresolved"):
            action = "merge"
        elif wanted in ("merge", "review") and task and pr:
            action = "review"
        elif wanted == "raise_budget":
            action = "raise_budget"
        elif task:
            action = "open_task"
        elif link:
            action = "open_issue"
        elif planning:
            action = of_plan
        else:
            action = "raise_budget" if goal["status"] == "budget_exhausted" else "open_issue"
        if replanning and action in ("approve_plan", "fix_plan"):
            continue
        out.append(
            {
                "what": need.get("what") or "",
                "ref": (link or {}).get("ref") or "",
                "action": action,
                "task_id": (task or {}).get("id") or "",
                "pr_url": pr,
                **({"changes": suggestion["changes"]} if action == "accept_fix" and suggestion else {}),
            }
        )
    # A fix waiting to be accepted is always asked about, whatever the model wrote.
    if suggestion and not any(n["action"] == "accept_fix" for n in out):
        out.insert(
            0,
            {
                "what": "I drafted a fix for the plan. Accept it?",
                "ref": "",
                "action": "accept_fix",
                "task_id": "",
                "pr_url": "",
                "changes": suggestion["changes"],
            },
        )
    return out
