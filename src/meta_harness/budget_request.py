"""A task held for budget, as a request: how much more it asks for, and why.

A task short of budget before a run holds and says so in its status reason (the RunTask workflow's `_grant`).
The reason is a sentence for a person. This module is the one place that writes that sentence and reads it
back, so the Task API can serve the same hold as numbers (`budget_request` on a task that waits on budget) and
the console can offer the amount in one click instead of leaving the operator to work it out. A test holds
the two directions together: what `hold_reason` writes, `parse` reads.
"""

import re
from decimal import ROUND_CEILING, Decimal
from typing import Any

from meta_harness.money import usd

_AMOUNT = r"\$(-?\d+(?:\.\d+)?)"
_HOLD = re.compile(
    rf"^(?P<what>.+?) needs about {_AMOUNT}, and (?P<why>.+)\. "
    r"Raise (?:the goal's budget|the task's spend limit), or end the task$",
    re.DOTALL,
)
_TASK_WHY = re.compile(rf"^the task has {_AMOUNT} left of its {_AMOUNT} spend limit$")
_GOAL_WHY = re.compile(
    rf"^goal (?P<goal>\S+) has spent {_AMOUNT} of its {_AMOUNT} budget, which leaves {_AMOUNT} "
    rf"for each of its (?P<running>\d+) running tasks(?: while {_AMOUNT} is held for runs under way)?$"
)
# The least a request asks for: a raise of cents would hold the task again at its next run.
LEAST_ADD_USD = Decimal(1)


def task_why(left: Any, limit: Any) -> str:
    """Why a run cannot start for the task's own spend limit."""
    return f"the task has {usd(left)} left of its {usd(limit)} spend limit"


def goal_why(goal_id: str, spent: Any, max_usd: Any, share: Any, running: int, granted: Any = "0") -> str:
    """Why a run cannot start for the goal's budget, shared among the goal's running tasks. granted: what the
    goal holds for its other tasks' runs under way, which is said when it is part of why."""
    why = (
        f"goal {goal_id} has spent {usd(spent)} of its {usd(max_usd)} budget, which "
        f"leaves {usd(share)} for each of its {running} running tasks"
    )
    return f"{why} while {usd(granted)} is held for runs under way" if Decimal(str(granted or 0)) > 0 else why


def for_goal(why: str) -> bool:
    """Whether the hold is for the goal's budget, not the task's spend limit."""
    return why.startswith("goal ")


def hold_reason(what: str, need: Any, why: str) -> str:
    """The status reason of a task held for budget: what the run needs, why it cannot have it, and the two
    answers."""
    raise_what = "the goal's budget" if for_goal(why) else "the task's spend limit"
    return f"{what} needs about {usd(need)}, and {why}. Raise {raise_what}, or end the task"


def parse(reason: str | None) -> dict[str, Any] | None:
    """A budget hold's status reason as a request, or None for any other text.

    For the task's spend limit: `for` "task", what the run is (`what`), what it needs (`needs_usd`), what the
    task has left and its limit (`left_usd`, `limit_usd`), and the request itself: `add_usd`, enough for the
    run in whole dollars and at least LEAST_ADD_USD, and `to_usd`, the limit that grants it. For the goal's
    budget: `for` "goal", `goal_id`, `needs_usd` and what the goal leaves each running task (`share_usd`); the
    goal's own budget resume works out the amount.
    """
    held = _HOLD.match((reason or "").strip())
    if not held:
        return None
    what, need, why = held["what"], Decimal(held[2]), held["why"]
    request: dict[str, Any] = {"what": what, "needs_usd": str(need)}
    if task := _TASK_WHY.match(why):
        left, limit = Decimal(task[1]), Decimal(task[2])
        add = max((need - left).to_integral_value(rounding=ROUND_CEILING), LEAST_ADD_USD)
        return request | {
            "for": "task",
            "left_usd": str(left),
            "limit_usd": str(limit),
            "add_usd": str(add),
            "to_usd": str(limit + add),
        }
    if goal := _GOAL_WHY.match(why):
        return request | {"for": "goal", "goal_id": goal["goal"], "share_usd": goal[4], "running": int(goal["running"])}
    return None
