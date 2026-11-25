"""What planning has spent on a goal without being asked, against the most it may.

One planning run has its own spend limit (the planning configuration's `max_usd`). But the planner starts
runs of its own after the one an operator asked for: one that continues a plan stopped at a limit, each resize
round, a fix for the plan's gaps. Each is the obvious next step and is taken without asking, and together
they had no limit. So they share one, `max_unasked_usd`: what planning may spend on a goal by itself.

The count starts again with each planning run an operator asks for (a new goal's first plan, Plan again), since
that is a person choosing to spend, and a replan can be as large as the plan before it. Reaching the limit
starts nothing more: the plan stays as it is, and its page asks the operator, whose asking starts the count
again.

A run is the planner's own when its external ref says so: `...:continue`, `...:resizeN`, `...:reviewN`,
`plan-fix:...`, `plan-review:...`. A review an operator asks for is `plan-review-asked:...`, and is theirs.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

# What planning may spend on a goal without asking, when the planning configuration does not say.
DEFAULT_MAX_UNASKED_USD = Decimal("12.00")
# The task types that are planning.
PLANNING_TYPES = ("plan",)
_UNASKED = re.compile(r"(:continue|:resize\d+|:review\d+)$|^plan-fix:|^plan-review:")


def is_unasked(task: dict) -> bool:
    """Whether the planner started this planning task itself, with no operator asking for it."""
    return bool(_UNASKED.search(str(task.get("external_ref") or "")))


def limit(config: dict[str, Any]) -> Decimal:
    """The most planning may spend on a goal without asking. A configuration from before the setting has none,
    and gets the default: a limit is never left off."""
    try:
        found = Decimal(str(config.get("max_unasked_usd", DEFAULT_MAX_UNASKED_USD)))
    except InvalidOperation:
        return DEFAULT_MAX_UNASKED_USD
    return found if found > 0 else DEFAULT_MAX_UNASKED_USD


def check(config: dict[str, Any]) -> list[str]:
    """What is wrong with the setting, for the planning configuration's check; empty when it is fine or absent."""
    if "max_unasked_usd" not in config:
        return []
    try:
        if Decimal(str(config["max_unasked_usd"])) > 0:
            return []
    except InvalidOperation:
        pass
    return ["max_unasked_usd must be an amount above 0: the most planning may spend on a goal without asking"]


def unasked(goal_id: str, tasks: Any, events: Any) -> Decimal:
    """What the planner's own runs have spent on the goal since the last planning run an operator asked for."""
    planning = [t for t in tasks.list(None, goal_id, 500) if t.get("task_type") in PLANNING_TYPES]
    planning.sort(key=lambda t: str(t.get("created_at") or ""))
    since = max((i for i, t in enumerate(planning) if not is_unasked(t)), default=-1)
    spent = Decimal(0)
    for task in planning[since + 1 :]:
        page = events.read(0, 5000, task_id=task["id"], event_type="attempt_finished")
        spent += sum((Decimal(str((e.get("payload") or {}).get("spent_usd") or 0)) for e in page["events"]), Decimal(0))
    return spent


def refusal(goal: dict, config: dict[str, Any], tasks: Any, events: Any) -> str | None:
    """Why the planner may not start another run of its own on the goal now; None when it may. Either planning
    has spent what it may without asking, or the goal's budget is used up."""
    most = limit(config)
    spent = unasked(goal["id"], tasks, events)
    if spent >= most:
        return (
            f"planning has spent ${spent.quantize(Decimal('0.01'))} of the ${most} it may spend on this goal "
            "without asking. Plan again on the plan's page starts the count again"
        )
    budget, used = goal.get("max_usd"), goal.get("spent_usd")
    if budget is not None and used is not None and Decimal(str(used)) >= Decimal(str(budget)):
        return f"the goal has spent its ${budget} budget. Raise the budget on the goal's page to plan further"
    return None
