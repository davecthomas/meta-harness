"""A goal's budget: what it does when it is reached, where the goal stands against it, and what a resume sets.

A goal with a budget (`max_usd`) says what the budget does once its runs have spent it:

- `block`, the default: the goal stops spending at its blocking line, `max_usd` plus `budget_overage_percent`
  of it (10 by default). Between the budget and that line the goal runs on, over budget.
- `warn`: no longer taken. A goal saved with it before every budget became a hard cap is read as `block`.

Every goal's budget is a hard cap at or below the deployment's ceiling, the task policy's `max_goal_usd`
($100 unless it says otherwise). A goal with no budget of its own is read as having the ceiling, one above it
as having the ceiling, and its blocking line never passes the ceiling: its overage shrinks to fit (`capped`).

Above every goal is the deployment's budget policy (the task policy's `budget_policy`). Under `track`, no
goal's budget stops anything, whatever its own mode: each behaves as `warn`, and spend is still recorded and
shown against it.

The scheduler's admission and the run service's budget hold read the blocking line from the goal the planner
serves (`budget_block_at`), so the line is worked out in one place. Nothing here reads or writes anything:
budget_api.py does that.
"""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

MODES = ("block", "warn")
DEFAULT_MODE = "block"
DEFAULT_OVERAGE_PERCENT = 10
CENT = Decimal("0.01")
# A task whose work is done, whatever became of its pull request: what it cost is what a task like it costs.
FINISHED_TASK_STATUSES = ("closed", "ready")
# An issue whose task is in one of these has no more to spend.
DONE_TASK_STATUSES = ("closed", "ready", "canceled")


def block_at(max_usd: Any, mode: str, overage_percent: int) -> Decimal | None:
    """The spend a block goal stops at. None for a goal with no budget, or one that only warns."""
    if max_usd is None or mode != "block":
        return None
    return (Decimal(str(max_usd)) * (100 + int(overage_percent)) / 100).quantize(CENT)


def state(max_usd: Any, mode: str, overage_percent: int, spent: Any, status: str = "active") -> str:
    """Where a goal stands against its budget: ok, over (past the budget, still running) or blocked."""
    if max_usd is None:
        return "ok"
    if status == "budget_exhausted":
        return "blocked"
    spent = Decimal(str(spent))
    line = block_at(max_usd, mode, overage_percent)
    if line is not None and spent >= line:
        return "blocked"
    return "over" if spent >= Decimal(str(max_usd)) else "ok"


def capped(goal: dict, ceiling: Decimal) -> dict:
    """The goal as its budget binds it: never without a budget, never only warning, and never able to spend past
    the ceiling. A goal saved before budgets became hard caps is read this way, so no stored row needs to
    change. budget_ceiling says the ceiling, for a page to show."""
    ceiling = Decimal(str(ceiling)).quantize(CENT)
    max_usd = Decimal(str(goal["max_usd"])) if goal.get("max_usd") is not None else ceiling
    max_usd = min(max_usd, ceiling)
    goal["max_usd"] = str(max_usd)
    if goal.get("budget_mode") != "block":
        goal["budget_mode"] = "block"
    # The overage that keeps the blocking line at or below the ceiling.
    room = ((ceiling / max_usd - 1) * 100).to_integral_value(rounding=ROUND_FLOOR) if max_usd > 0 else Decimal(0)
    goal["budget_overage_percent"] = max(0, min(int(goal.get("budget_overage_percent") or 0), int(room)))
    goal["budget_ceiling"] = str(ceiling)
    return goal


def mode_in_effect(goal: dict) -> str:
    """What the goal's budget does now: its own mode, or warn while the deployment's budget policy is track."""
    return "warn" if goal.get("budget_policy") == "track" else goal["budget_mode"]


def view(goal: dict, policy: str = "block", ceiling: Decimal | None = None) -> dict:
    """The goal with the deployment's budget_policy, budget_block_at and budget_state, as the API serves it,
    held to the ceiling when one is given (capped). budget_resume_to shows only for a goal that is over or
    blocked."""
    if ceiling is not None:
        capped(goal, ceiling)
    goal["budget_policy"] = policy
    mode = mode_in_effect(goal)
    line = block_at(goal["max_usd"], mode, goal["budget_overage_percent"])
    goal["budget_block_at"] = str(line) if line is not None else None
    # A goal the policy no longer stops reads as over, not blocked, until its next sweep makes it active.
    status = goal["status"] if mode == "block" else "active"
    goal["budget_state"] = state(goal["max_usd"], mode, goal["budget_overage_percent"], goal["spent_usd"], status)
    stopped = goal["budget_state"] == "blocked" or (goal["budget_state"] == "over" and mode == "block")
    if not stopped:
        goal["budget_resume_to"] = None
    return goal


def resume_to(spent: Decimal, finished_costs: list[Decimal], issues_left: int, default_task_usd: Decimal) -> dict:
    """The budget a resume sets: what the goal has spent, plus what its issues not yet done are likely to cost,
    rounded up to a whole dollar. Each is taken to cost the average of the goal's finished tasks, or the default
    task spend limit when none has finished. Returns the budget and how it was worked out."""
    if finished_costs:
        each = (sum(finished_costs, Decimal(0)) / len(finished_costs)).quantize(CENT)
        basis = f"its {len(finished_costs)} finished tasks cost ${each} each on average"
    else:
        each = Decimal(default_task_usd).quantize(CENT)
        basis = f"none of its tasks has finished, so each is taken at the default task limit, ${each}"
    budget = (spent + each * issues_left).to_integral_value(rounding=ROUND_CEILING)
    if budget <= spent:
        # A whole-dollar spend with no issue left: the budget must still be above what is spent.
        budget += 1
    spent = spent.quantize(CENT)
    how = f"${spent} spent, plus {issues_left} issues not yet done at ${each} each ({basis}), rounded up"
    return {"to": str(budget), "spent_usd": str(spent), "issues_left": issues_left, "each_usd": str(each), "how": how}


def worked_out(
    spent: Decimal, tasks: list[dict], costs: dict[str, Decimal], links: list[dict], default: Decimal
) -> dict:
    """resume_to from the goal's tasks, what each spent, and its issue links."""
    by_id = {task["id"]: task for task in tasks}
    finished = [
        costs[task["id"]]
        for task in tasks
        # A planning task drafts issues: it says nothing of what an issue costs.
        if task["status"] in FINISHED_TASK_STATUSES
        and task.get("task_type") != "plan"
        and costs.get(task["id"], Decimal(0)) > 0
    ]
    left = sum(
        1
        for link in links
        if link["state"] == "open"
        and (by_id.get(link.get("task_id") or "") or {}).get("status") not in DONE_TASK_STATUSES
    )
    return resume_to(spent, finished, left, default)
