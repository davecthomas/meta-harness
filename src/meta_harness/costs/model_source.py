"""Model cost, harvested from the events that record it.

A run's cost is its `attempt_finished` event: what the run's grant counted, the figure every other total in
the meta-harness uses. The planner's own model calls are `goal_drafted` and `goal_titled`. Each harvest reads
the events after the position the last one reached, and saves their costs with the new position in one
transaction, so an event is counted once however a harvest ends.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

import psycopg

from meta_harness.costs import store
from meta_harness.costs.store import Cost

SOURCE = "model"
EVENT_TYPES = "attempt_finished,goal_drafted,goal_titled"
PAGE = 2000


def model_of(candidate: str) -> tuple[str, str]:
    """The engine and model of a candidate, `harness:<profile>/<engine>:<model>`, or of `<engine>:<model>`."""
    engine, _, model = candidate.rpartition("/")[2].partition(":")
    return (engine, model) if model else ("", engine)


def kind_of(task: dict | None, tier: str | None) -> str:
    """What kind of run the money went to: a review, a fix, planning, or a task's own run."""
    if tier in ("review", "fix"):
        return tier
    return "planning" if (task or {}).get("task_type") == "plan" else "task"


def _day(event: dict) -> date:
    return datetime.fromisoformat(str(event["time_utc"]).replace("Z", "+00:00")).date()


def harvest(
    conn: psycopg.Connection,
    events: Any,
    task: Callable[[str], dict | None],
    goal: Callable[[str], dict | None],
) -> int:
    """Harvest every model cost recorded since the last harvest. task and goal read one by id, or None when
    it is gone. Returns how many events were counted."""
    counted = 0
    position = store.position(conn, SOURCE)
    while True:
        page = events.read(from_position=position, limit=PAGE, event_type=EVENT_TYPES)
        found = page["events"]
        if not found:
            return counted
        costs = []
        for event in found:
            payload = event.get("payload") or {}
            if event["event_type"] == "attempt_finished":
                usd = Decimal(str(payload.get("spent_usd") or 0))
                spec = task(event.get("task_id") or "") or {}
                owner = goal(spec.get("goal_id") or "") or {}
                engine, model = model_of(str(payload.get("candidate") or ""))
                cost = Cost(
                    day=_day(event),
                    source=SOURCE,
                    item=kind_of(spec, payload.get("tier")),
                    usd=usd,
                    goal_id=spec.get("goal_id") or "",
                    task_id=event.get("task_id") or "",
                    repo=spec.get("repo") or "",
                    model=model,
                    engine=engine,
                    # Who the work was for: the operator who created the goal, else whoever submitted the task.
                    operator=owner.get("created_by") or spec.get("caller") or "",
                )
            else:
                usd = Decimal(str(payload.get("usd_cost") or 0))
                engine, model = model_of(str(payload.get("model") or ""))
                owner = goal(payload.get("goal_id") or "") or {}
                cost = Cost(
                    day=_day(event),
                    source=SOURCE,
                    item="planning",
                    usd=usd,
                    goal_id=payload.get("goal_id") or "",
                    repo=(owner.get("repos") or [""])[0],
                    model=model,
                    engine=engine,
                    operator=owner.get("created_by") or event.get("actor") or "",
                )
            if usd:
                costs.append(cost)
        position = max(int(event["position"]) for event in found)
        store.add(conn, SOURCE, costs, position)
        counted += len(found)
        if len(found) < PAGE:
            return counted
