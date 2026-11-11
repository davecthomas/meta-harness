"""A goal's budget: what it does when it is reached, where the goal stands against it, and what a resume sets.

A goal with a budget (`max_usd`) says what the budget does once its runs have spent it:

- `block`, the default: the goal stops spending at its blocking line, `max_usd` plus `budget_overage_percent`
  of it (10 by default). Between the budget and that line the goal runs on, over budget.
- `warn`: the goal keeps going. Reaching the budget is recorded and shown, and nothing is held or refused.

The scheduler's admission and the run service's budget hold read the blocking line from the goal the planner
serves (`budget_block_at`), so the line is worked out in one place. Nothing here reads or writes anything:
budget_api.py does that.
"""

from decimal import ROUND_CEILING, Decimal
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


def view(goal: dict) -> dict:
    """The goal with budget_block_at and budget_state, as the API serves it. budget_resume_to shows only for a
    goal that is over or blocked."""
    line = block_at(goal["max_usd"], goal["budget_mode"], goal["budget_overage_percent"])
    goal["budget_block_at"] = str(line) if line is not None else None
    goal["budget_state"] = state(
        goal["max_usd"], goal["budget_mode"], goal["budget_overage_percent"], goal["spent_usd"], goal["status"]
    )
    stopped = goal["budget_state"] == "blocked" or (goal["budget_state"] == "over" and goal["budget_mode"] == "block")
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
