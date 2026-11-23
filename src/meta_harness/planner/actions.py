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


# The most actions one goal is given at once, for a page that lists many goals.
MAX_FOR_GOAL = 3


def for_goal(
    goal: dict,
    links: list[dict],
    tasks: list[dict],
    plan: dict | None = None,
    suggestion: dict | None = None,
    most: int = MAX_FOR_GOAL,
) -> list[dict]:
    """What the goal needs from the operator now, from its records alone, each as the action a page offers:
    most urgent first, `MAX_FOR_GOAL` at most. No model has a say, so a page that lists goals (the Overview)
    shows buttons that are true at the moment it is read, whenever its words were written.

    Each is what `actionable` answers, with the goal it is about (`goal_id`) and that goal's label, which
    splitting a held issue needs."""
    wanted: list[dict] = []
    # Stopped by its budget, or past it and still running: either way the budget is the operator's to raise.
    if goal["status"] == "budget_exhausted" or goal.get("budget_state") in ("blocked", "over"):
        wanted.append({"what": "", "ref": "", "action": "raise_budget"})
    if goal["status"] == "planning" and plan is not None:
        # `actionable` turns this into the plan's fix when it cannot be approved, and drops it while the
        # planner writes a new version.
        wanted.append({"what": "", "ref": "", "action": "approve_plan"})
    by_id = {task["id"]: task for task in tasks}
    held = []
    for link in links:
        if link.get("state") != "open" or splits.is_replaced(link):
            continue
        task = by_id.get(link.get("task_id") or "")
        if task is None:
            if (link.get("held_reason") or link.get("refused_reason")) and not _split_already(link):
                held.append({"what": "", "ref": link["ref"], "action": "open_issue"})
            continue
        if waits_on_operator(task, bool(goal.get("auto_merge"))):
            wanted.append({"what": "", "ref": link["ref"], "action": "open_task"})
    found = actionable([*wanted, *held], goal, links, tasks, suggestion, plan)
    # One decision on a plan at a time: while the planner's fix waits, approving would approve the plan
    # without it. The fix is decided first, and the approval is offered on the version that follows.
    if any(need["action"] == "accept_fix" for need in found):
        found = [need for need in found if need["action"] != "approve_plan"]
    by_ref = {link["ref"]: link for link in links}
    return [
        {
            **need,
            # What the button beside it is for, said from the records: a button is never shown bare.
            "what": said(need, goal, by_ref.get(need["ref"]), by_id.get(need["task_id"]), plan),
            "asks": asks(need, goal, by_id.get(need["task_id"])),
            "title": _title(by_ref.get(need["ref"]), by_id.get(need["task_id"])),
            "since": ((by_id.get(need["task_id"]) or {}).get("standing") or {}).get("since") or "",
            "goal_id": goal["id"],
            "label": goal.get("label") or "",
        }
        for need in found[:most]
    ]


def waits_on_operator(task: dict, auto_merge: bool = False) -> bool:
    """Whether the task is the operator's to move. The Task Manager looks at every open task and records who
    has it (`standing`): its word decides, so a failed task it is about to retry, or one of a goal that is
    not running, is not the operator's whatever its status says. Only a task it has not looked at yet is
    judged by its status."""
    standing = task.get("standing")
    if standing:
        return standing.get("owner") == "operator" and standing.get("situation") != "goal_not_active"
    status = task.get("status")
    ready = status == "ready" and bool(task.get("pr_url")) and not auto_merge
    return bool(task.get("waiting_on")) or status in ("failed", "review_unresolved") or ready


def for_task(task: dict) -> list[dict]:
    """What a task of no goal needs from the operator now: one action, or none. A goal's tasks are in
    `for_goal`, which this follows."""
    if task.get("status") in ("closed", "canceled") or not waits_on_operator(task):
        return []
    ref = task.get("external_ref") or f"task:{task['id']}"
    alone = {"id": "", "status": "active", "label": ""}
    link = {"ref": ref, "task_id": task["id"], "state": "open", "title": task.get("title")}
    return for_goal(alone, [link], [task])


def _title(link: dict | None, task: dict | None) -> str:
    """What an action is about, by name: the task's short name or title, else its issue's."""
    return str((task or {}).get("name") or (task or {}).get("title") or (link or {}).get("title") or "")


# What each action asks of the operator, in a word or three: the label a list of needs leads each with.
ASKS = {
    "approve_plan": "Approve the plan",
    "fix_plan": "Fix the plan",
    "accept_fix": "Update the plan",
    "merge": "Merge",
    "settle_review": "Settle the review",
    "retry": "Retry or fix",
    "split_or_run": "Split or run",
    "raise_budget": "Raise the budget",
    "review": "Review the changes",
    "open_issue": "Look at the issue",
    "open_task": "Look at",
}


def asks(need: dict, goal: dict, task: dict | None) -> str:
    """The need in a word or three, named for what is asked, since "needs you" alone says nothing."""
    waiting = (task or {}).get("waiting_on")
    if need["action"] == "open_task" and waiting == "budget":
        request = (task or {}).get("budget_request") or {}
        if request.get("for") == "task" and request.get("add_usd"):
            return f"Grant ${float(request['add_usd']):.2f} more"
        return "Raise the goal's budget" if request.get("for") == "goal" else "Raise the spend limit"
    if need["action"] == "open_task" and waiting == "operator_review":
        return "Finish the review"
    if need["action"] == "open_task" and waiting == "review":
        return "Decide on the review"
    if need["action"] == "raise_budget" and goal.get("status") != "budget_exhausted":
        return "Over budget" if goal.get("budget_state") == "over" else ASKS["raise_budget"]
    return ASKS.get(need["action"], "Look at")


def _short(text: object, most: int = 140) -> str:
    """One line of a reason, cut at a word, without its last full stop."""
    line = " ".join(str(text or "").split()).rstrip(".")
    return line if len(line) <= most else line[:most].rsplit(" ", 1)[0] + "…"


def said(need: dict, goal: dict, link: dict | None, task: dict | None, plan: dict | None) -> str:
    """One sentence on what an action is about, from the goal's records, to read beside its button. The page
    that lists goals shows it, so "the fix" or "the plan" is never a button with nothing to say which."""
    name = (task or {}).get("name") or (task or {}).get("title") or (link or {}).get("title") or "A task"
    action = need["action"]
    if action == "accept_fix":
        return "The planner drafted a fix for gaps in this goal's plan. Accepting it makes a new plan version."
    if action == "approve_plan":
        count = len((plan or {}).get("drafts") or [])
        tasks_said = f", with {count} new task{'s' if count != 1 else ''}" if count else ""
        return f"The plan is ready{tasks_said}. Nothing runs until you approve it."
    if action == "fix_plan":
        why = _short(next(iter((plan or {}).get("invalid") or []), ""))
        return f"The plan can't be approved yet{': ' + why if why else ''}."
    if action == "raise_budget":
        if goal.get("status") != "budget_exhausted" and goal.get("budget_state") == "over":
            return f"The goal is past its budget of ${goal.get('max_usd')} and keeps running."
        return f"The goal has spent its budget of ${goal.get('max_usd')}, so its work has stopped."
    if action == "merge":
        return f"{name}: its pull request passed checks and review."
    if action == "settle_review":
        return f"{name}: the review found things no fix settled."
    if action == "retry":
        why = _short((task or {}).get("status_reason"))
        return f"{name} failed{': ' + why if why else ''}."
    if action == "split_or_run":
        return f"{name} is held as too large for one pull request."
    waiting = (task or {}).get("waiting_on")
    if action == "open_task" and waiting == "budget":
        return f"{name} reached its spend limit{': ' + _short(task.get('status_reason')) if task.get('status_reason') else ''}."
    if action == "open_task" and waiting == "operator_review":
        return f"{name} waits for the review {task.get('review_claimed_by') or 'an operator'} took over."
    if action == "open_task" and waiting == "review":
        return f"{name}: its review asks for changes and waits for you."
    if action == "open_task" and waiting:
        return f"{name} waits on your answer: {_short(waiting)}."
    standing = (task or {}).get("standing") or {}
    if action == "open_task" and standing.get("reason"):
        ask = f" {_short(standing['ask'])}." if standing.get("ask") else ""
        return f"{name}: {_short(standing['reason'])}.{ask}"
    if action == "open_issue":
        why = _short((link or {}).get("refused_reason") or (link or {}).get("held_reason"))
        return f"{name} is not running{': ' + why if why else ''}."
    return f"{name} needs a look."


# What an answer may say it suggests, besides `ACTIONS`: looking at a goal's plan, at a goal, or at what a
# finished goal could lead to. None of these changes anything: each opens the goal's page.
LOOKS = ("open_plan", "open_goal", "next_steps")
SUGGESTABLE = (*ACTIONS, *LOOKS)
# The actions a goal's records may answer each suggested one with. A suggestion to approve becomes the plan's
# fix when the plan cannot be approved; one to merge becomes the review's settling when the review is unsettled.
_ANSWERS = {
    "approve_plan": ("approve_plan", "fix_plan", "accept_fix"),
    "accept_fix": ("accept_fix",),
    "merge": ("merge", "settle_review"),
    "review": ("merge", "settle_review", "review"),
    "open_task": ("retry", "settle_review", "merge", "open_task"),
    "raise_budget": ("raise_budget",),
    "open_issue": ("split_or_run", "open_issue"),
}
MAX_SUGGESTED = 2


def suggested(wanted: str, goal: dict, needs: list[dict]) -> list[dict]:
    """The buttons for one step an answer suggests about a goal. wanted: the action the model named for its
    own words. needs: what the goal's records say it needs now (for_goal, uncut).

    A step that changes something is offered only as the records support it: the goal's needs of that kind,
    `MAX_SUGGESTED` at most. When the records hold none, the suggestion is not dropped, since the answer still
    said it: the button opens the goal, where the operator sees why. A step that only looks opens the goal's
    page, named for what the answer said to look at."""
    look = {"action": "open_goal", "says": "goal", "what": "", "ref": "", "task_id": "", "pr_url": ""}
    look |= {"goal_id": goal["id"], "label": goal.get("label") or ""}
    if wanted == "open_plan" and goal["status"] == "planning":
        return [{**look, "says": "plan"}]
    if wanted == "next_steps" and goal["status"] in ("met", "canceled"):
        return [{**look, "says": "next_steps"}]
    matched = [need for need in needs if need["action"] in _ANSWERS.get(wanted, ())]
    return matched[:MAX_SUGGESTED] or [look]
