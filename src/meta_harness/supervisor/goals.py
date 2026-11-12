"""Where a goal stands, from where its tasks stand: moving, waiting on an operator, or done.

A goal's status says what an operator set it to (active, paused) or what the planner found (met, stopped by
its budget). It does not say whether the goal is getting anywhere. An active goal whose every remaining task
failed, or waits to be merged by hand, is active and going nowhere, and the design's `stalled` status for
that was never set by anything. This reads the same thing from the supervisor's record instead of a status:
a goal with work left, none of it moving, and some of it an operator's, waits on that operator.
"""

from typing import Any

# What a goal's standing can be.
#   moving      at least one of its tasks is being worked, or will be, with no one's help
#   needs_you   it has work left, none of it is moving, and an operator has what is left
#   held        the goal itself is not running: paused, planning, or stopped by its budget
#   idle        it is active with nothing left to do and nothing waiting: its next sweep decides
#   done        it is met or canceled
STATES = ("moving", "needs_you", "held", "idle", "done")
# The owners under which a task is moving without an operator.
MOVING_OWNERS = ("workflow", "scheduler", "supervisor", "planner")
# Tasks that are finished for the goal's purposes, whoever has them.
FINISHED = ("closed", "canceled")

HELD = {
    "paused": ("it is paused", "Resume the goal to let its tasks start."),
    "planning": ("it is planning", "Approve its plan, or ask for a new one, on its page."),
    "stalled": ("it is stalled", "Resume the goal."),
    "budget_exhausted": ("it is stopped by its budget", "Raise its budget on its page to let it go on."),
}


def goal_standing(status: str, tasks: list[dict[str, Any]], refused: int = 0) -> dict[str, Any]:
    """The goal's standing: {state, reason, ask, yours, moving}.

    tasks are the goal's tasks as the Task API serves them, each with its `standing`. refused is how many of
    its open issues the planner refused. yours lists the tasks an operator has, each with what to do, longest
    waiting first, so a page can show them without asking again.
    """
    if status in ("met", "canceled"):
        return _standing("done", f"it is {status}", "", [], 0)
    open_tasks = [t for t in tasks if t.get("status") not in FINISHED]
    yours = sorted(
        (
            {
                "task_id": t["id"],
                "title": t.get("title") or t["id"],
                "reason": t["standing"]["reason"],
                "ask": t["standing"].get("ask") or "",
                "since": t["standing"]["since"],
            }
            for t in open_tasks
            if (t.get("standing") or {}).get("owner") == "operator"
            and t["standing"].get("situation") != "goal_not_active"
        ),
        key=lambda found: found["since"],
    )
    moving = sum(
        1
        for t in open_tasks
        # A task the supervisor has not looked at yet is taken as moving: its first pass says otherwise.
        if t.get("standing") is None or t["standing"].get("owner") in MOVING_OWNERS
    )
    if status in HELD:
        reason, ask = HELD[status]
        return _standing("held", reason, ask, yours, moving)
    if moving:
        return _standing(
            "moving", f"{_count(moving, 'task is', 'tasks are')} being worked or waiting to start", "", yours, moving
        )
    if yours or refused:
        waiting = []
        if yours:
            waiting.append(f"{_count(len(yours), 'task waits', 'tasks wait')} on you")
        if refused:
            waiting.append(f"{_count(refused, 'issue was', 'issues were')} refused")
        return _standing(
            "needs_you",
            f"nothing in it is moving: {' and '.join(waiting)}",
            "Each one below says what to do. The goal goes on as soon as one of them is unstuck.",
            yours,
            0,
        )
    return _standing("idle", "it has no task in progress and none waiting on you", "", [], 0)


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _standing(state: str, reason: str, ask: str, yours: list[dict], moving: int) -> dict[str, Any]:
    return {"state": state, "reason": reason, "ask": ask, "yours": yours, "moving": moving}
