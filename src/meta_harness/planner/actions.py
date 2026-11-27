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
        # Nor while a second model is checking this version: its findings may change the plan.
        if (replanning or (plan or {}).get("reviewing")) and action in ("approve_plan", "fix_plan"):
            continue
        out.append(
            {
                "what": need.get("what") or "",
                "ref": (link or {}).get("ref") or "",
                "action": action,
                "task_id": (task or {}).get("id") or "",
                "pr_url": pr,
                **(_fix_facts(suggestion) if action == "accept_fix" and suggestion else {}),
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
                **_fix_facts(suggestion),
            },
        )
    return out


def _usd(amount: object) -> str:
    """Dollars as a person says them: $62, $3.50."""
    text = f"{float(amount):.2f}"
    return "$" + (text[:-3] if text.endswith(".00") else text)


def _older(version: object, current: object) -> bool:
    """Whether a version is older than the current one."""
    try:
        return int(version) < int(current)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return False


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def plan_decision(
    goal: dict,
    plan: dict | None,
    glance: dict | None = None,
    suggestion: dict | None = None,
    planning_usd: object = None,
    review: dict | None = None,
    review_usd: object = None,
) -> dict:
    """The plan's state and the one thing it asks of the operator now, from the goal's records: what the Plan
    page's decision bar says, in two sentences at most, and its buttons, the recommended one first.

    Each button is an action the page performs and a label that names it; one that spends says the most it
    may spend. A state that asks nothing has no buttons. The page draws this and decides nothing itself, so
    it cannot disagree with what `for_goal` lists as needing the operator.

    glance: the plan's figures (plan_view.glance). planning_usd: the spend limit of one planning run. review:
    the plan's adversarial review (plan_review.current), when it has one. review_usd: the spend limit of one
    review, named on the button that runs another after a canceled one.

    While a review runs the decision has a link (Approve now): a way through that is not the recommended
    course, so it is no button."""
    again = f" (up to {_usd(planning_usd)})" if planning_usd is not None else ""
    replan = {"action": "replan", "label": f"Plan again{again}"}
    drafting = bool(goal.get("plan_task_id"))
    if plan is None:
        if drafting:
            return {"state": "drafting", "says": "The planner is writing the plan.", "buttons": []}
        return {"state": "none", "says": "This goal has no plan.", "buttons": []}
    tasks = len(plan.get("drafts") or [])
    if goal["status"] != "planning":
        return {
            "state": "approved",
            "says": f"This plan is approved. Its {_count(tasks, 'task')} are the goal's work now.",
            "buttons": [],
        }
    if drafting:
        return {
            "state": "improving",
            "says": f"The planner is revising the plan. It has {_count(tasks, 'task')} so far.",
            "buttons": [],
        }
    if review and review.get("state") == "running" and review.get("version") == plan.get("version"):
        # Waiting is the recommended course, so no button is offered while the reviewer reads.
        return {
            "state": "improving",
            "says": f"{review.get('model') or 'A second model'} is checking the plan for problems.",
            "buttons": [],
            "link": {"action": "approve_plan", "label": "Approve now"},
        }
    if suggestion:
        gaps = len(suggestion.get("fixes") or [])
        return {
            "state": "fix_waiting",
            "says": f"The planner drafted a fix for {_count(gaps, 'gap')} in this plan.",
            "buttons": [
                {"action": "accept_fix", "label": "Accept the fix"},
                {"action": "dismiss_fix", "label": "Keep the plan as it is"},
            ],
        }
    if plan.get("stopped_at"):
        return {
            "state": "incomplete",
            "says": f"Planning stopped at its {plan['stopped_at']} limit with {_count(tasks, 'task')} drafted.",
            "buttons": [{"action": "replan", "label": f"Carry on planning{again}"}],
        }
    if plan.get("invalid"):
        reasons = plan["invalid"]
        more = f" And {len(reasons) - 1} more." if len(reasons) > 1 else ""
        return {
            "state": "cannot_approve",
            "says": f"This plan cannot be approved yet: {str(reasons[0]).rstrip('.')}.{more}",
            "buttons": [replan],
        }
    # A review canceled on an older version leaves the current one unchecked: offer another.
    rereview = []
    if review and review.get("state") == "canceled" and _older(review.get("version"), plan.get("version")):
        label = f"Review again (up to {_usd(review_usd)})" if review_usd is not None else "Review again"
        rereview = [{"action": "review_plan", "label": label}]
    figures = glance or {}
    repos = len(figures.get("repos") or [])
    spend = figures.get("may_spend_usd")
    still = [
        f for f in (review or {}).get("findings") or [] if f.get("status") == "open" and f.get("severity") == "blocking"
    ]
    if still:
        # The review left problems the planner did not settle: the decision is about those, said in full only
        # when there is one. Having the planner fix them is the recommended step, and approving stays open.
        found = (review.get("counts") or {}).get("found") or len(still)
        n = len(still)
        says = f"The review found {_count(found, 'problem')}, and {n} {'is' if n == 1 else 'are'} still open."
        first = str(still[0].get("says") or "").strip()
        if n == 1 and first and len(says) + len(first) < 270:
            says += f" {first}"
        return {
            "state": "ready",
            "says": says,
            "buttons": [
                {"action": "fix_findings", "label": f"{'Fix it' if n == 1 else f'Fix all {n}'}{again}"},
                # The sentence says what the plan may spend; an amount on the button read as the goal's limit.
                {"action": "approve_plan", "label": f"Approve with {n} open"},
                *rereview,
            ],
        }
    says = f"{_count(tasks, 'task')} in {_count(repos, 'repo')}." if repos else f"{_count(tasks, 'task')}."
    approve = "Approve and start"
    buttons = [replan]
    if spend is not None and float(spend) > 0:
        budget = figures.get("budget_usd")
        if budget is not None and float(spend) > float(budget):
            # A budget is a hard cap: the plan may need more than the goal can spend. The operator should know
            # before approving that work stops there, and the button names what approving can spend, the budget.
            says += f" All of them may need up to {_usd(spend)}, and your {_usd(budget)} budget stops work before that."
            buttons = [{"action": "raise_budget", "label": "Raise the budget"}, replan]
        else:
            of_budget = f" of your {_usd(budget)} budget" if budget is not None else ""
            says += f" Running them may spend up to {_usd(spend)}{of_budget}."
    return {
        "state": "ready",
        "says": says,
        "buttons": [{"action": "approve_plan", "label": approve}, *rereview, *buttons],
    }


# The most actions one goal is given at once, for a page that lists many goals.
MAX_FOR_GOAL = 3


def for_goal(
    goal: dict,
    links: list[dict],
    tasks: list[dict],
    plan: dict | None = None,
    suggestion: dict | None = None,
    most: int = MAX_FOR_GOAL,
    bases: list[dict] | None = None,
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
    # A base branch that fails its checks, which this goal's operator is asked to have fixed (base_fix.py).
    found += [
        {"what": base_said(base), "ref": "", "action": "fix_base", "task_id": "", "pr_url": ""} for base in bases or []
    ]
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


def base_said(base: dict) -> str:
    """One sentence on a failing base the operator is asked about (planner.base_fix's row)."""
    count = len(base.get("checks") or [])
    name = str(base.get("repo") or "The repo").rsplit("/", 1)[-1]
    return (
        f"{name}'s base branch fails {count} check{'' if count == 1 else 's'} in the sandbox, before any "
        "task's change. Each task is told so, and one task can fix it."
    )


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


def for_settings(reports: list[dict]) -> list[dict]:
    """What a service's bad settings need: one action for each setting that fell back to its default
    (fallback.py), from each service's newest `settings_checked` report. An empty report clears its service's."""
    found = []
    for report in reports:
        service = str(report.get("service") or "a service")
        for item in report.get("defaulted") or []:
            setting = str(item.get("setting") or "a setting")
            found.append(
                {
                    "what": (
                        f"The {service} runs on {item.get('default')} for {setting}, since it is set to "
                        f"{item.get('value')!r}. {item.get('fix')}."
                    ),
                    "ref": "",
                    "action": "fix_setting",
                    "task_id": "",
                    "pr_url": "",
                    "asks": ASKS["fix_setting"],
                    "title": setting,
                    "goal_id": "",
                }
            )
    return found


def _title(link: dict | None, task: dict | None) -> str:
    """What an action is about, by name: the task's short name or title, else its issue's."""
    return str((task or {}).get("name") or (task or {}).get("title") or (link or {}).get("title") or "")


def _fix_facts(suggestion: dict) -> dict:
    """What a need to accept a fix says of it: why (each gap with its fix), and its changes as counts."""
    return {"changes": suggestion["changes"], "fixes": list(suggestion.get("fixes") or [])}


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
    # A setting with a safe default that was set badly, so its service runs on the default (fallback.py).
    "fix_setting": "Fix a setting",
    # A repo's base branch fails its standing checks, and a goal that asks before it acts found it.
    "fix_base": "Fix the base branch",
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
    if action == "fix_base":
        return need.get("what") or "The base branch fails its checks in the sandbox."
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
