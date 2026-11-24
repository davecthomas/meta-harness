"""What needs the operator, across every goal and task: the one list every page reads.

    GET /v1/needs    everything that waits on the operator now, most urgent first

The console once worked this out in seven places, each by its own rules: the browser for the Overview's list
and the header's inbox, the Task Manager for a goal's badge, two prompts' facts for the planner's words, and
more for the buttons. They disagreed on one page: the Overview's report said a plan waited for approval while
the list under it said nothing needed anyone. So one module decides, from the records, and every page that
says or counts what needs the operator reads its answer. A model may write the words around it. It does not
decide whether something needs the operator, or what the button is.

What decides: `actions.for_goal` for a goal (its plan, the planner's fix, its budget, its tasks and its held
issues) and `actions.for_task` for a task of no goal. For a task, the Task Manager's record of who has it
is the word (`actions.waits_on_operator`). Each item has what it asks (`asks`), what it is about (`title`),
one sentence on why (`what`), the action a page offers for it, and the goal and task it concerns.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI

from meta_harness import db
from meta_harness.planner import actions, budget, plans, store

log = logging.getLogger("meta_harness.planner")

# The goals that can need the operator: every one that is not over. A paused goal's work waits on its resume.
OPEN = ("planning", "active", "stalled", "budget_exhausted")
MAX_GOALS = 200
# Every need of a goal, not the few a row on a page shows.
ALL = 50


def of_goal(goal: dict, tasks: list[dict], most: int = ALL) -> list[dict]:
    """What the goal needs from the operator now, from its records and these tasks of its (actions.for_goal)."""
    from meta_harness.planner import suggestions

    with db.connect(autocommit=True) as conn:
        links, plan = store.links(conn, goal["id"]), plans.latest(conn, goal["id"])
    suggestion = suggestions.current(goal["id"]) if goal["status"] == "planning" else None
    if "budget_state" not in goal:
        goal = budget.view(dict(goal), goal.get("budget_policy") or "block")
    return actions.for_goal(goal, links, tasks, plan, suggestion, most)


def by_id(goal_id: str, tasks: Any, most: int = ALL) -> tuple[dict, list[dict]]:
    """The goal, and what it needs now. tasks: the Task API client."""
    with db.connect(autocommit=True) as conn:
        goal = store.get(conn, goal_id)
    return goal, of_goal(goal, tasks.list(None, goal_id, 500), most)


def plan_waits(found: list[dict]) -> bool:
    """Whether a goal's plan is what waits on the operator: its approval, its fix, or the planner's fix to it."""
    return any(need["action"] in ("approve_plan", "fix_plan", "accept_fix") for need in found)


# How many of the newest settings reports are read: each service starts often enough that its newest is among them.
SETTINGS_REPORTS = 100


def settings_reports(events: Any) -> list[dict]:
    """Each service's newest `settings_checked` report: which of its settings fell back to their default."""
    page = events.read(0, SETTINGS_REPORTS, before_position=2**62, event_type="settings_checked")["events"]
    newest: dict[str, dict] = {}
    for event in sorted(page, key=lambda e: e["position"]):
        payload = event.get("payload") or {}
        newest[str(payload.get("service") or "")] = payload
    return list(newest.values())


def gather(tasks: Any, events: Any = None) -> list[dict]:
    """Everything that needs the operator now: each open goal's needs, then each task of no goal that waits
    on the operator, then each setting a service runs on the default for. A goal that cannot be read is logged
    and left out; the rest are still listed."""
    found: list[dict] = []
    with db.connect(autocommit=True) as conn:
        goals = store.list_goals(conn, list(OPEN))[:MAX_GOALS]
    for goal in goals:
        title = goal.get("title") or (goal.get("statement") or "")[:120]
        try:
            needs = of_goal(goal, tasks.list(None, goal["id"], 500))
        except Exception as err:  # noqa: BLE001 - one goal's trouble does not hide the others' needs
            log.warning("could not read what goal %s needs: %s: %s", goal["id"], type(err).__name__, err)
            continue
        found += [{**need, "goal": title} for need in needs]
    try:
        alone = [t for t in tasks.held_by("operator") if not t.get("goal_id")]
    except Exception as err:  # noqa: BLE001 - the goals' needs are still the answer
        log.warning("could not read the tasks of no goal that wait on the operator: %s", err)
        alone = []
    for task in alone:
        found += [{**need, "goal": ""} for need in actions.for_task(task)]
    if events is not None:
        try:
            found += [{**need, "goal": ""} for need in actions.for_settings(settings_reports(events))]
        except Exception as err:  # noqa: BLE001 - the goals' and tasks' needs are still the answer
            log.warning("could not read the services' settings reports: %s", err)
    return [{**need, "key": _key(need)} for need in found]


def _key(need: dict) -> str:
    """A need's name for a page to keep its row by: the task, else the issue, else the goal and what is asked."""
    about = need.get("task_id") or need.get("ref") or need.get("goal_id") or need.get("title") or ""
    return f"{need['action']}:{about}"


def register(app: FastAPI, operator: Callable[..., str], tasks: Any, events: Any = None) -> None:
    """Adds the route. tasks: the Task API client; events: the event client, for the services' settings reports."""

    @app.get("/v1/needs")
    async def needs(who: str = Depends(operator)) -> dict:
        """Everything that needs the operator now, and how many: the one list the console's Needs you, its
        inbox and its counts are drawn from."""
        found = await asyncio.to_thread(gather, tasks, events)
        return {"needs": found, "count": len(found)}
