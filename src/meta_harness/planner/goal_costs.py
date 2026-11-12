"""What each goal has cost, for the goal list: the same total as a goal's Spend section, read for every goal at once.

A goal's Spend section (`GET /v1/goals/{id}/spend`) adds up its tasks' runs: each run's `attempt_finished`
event, the run service's own count from the run's grant. The goal list shows that total for each goal it
lists, so the two never disagree. Asking the Spend route once per row would read every task's events again
for each goal, so the list reads every task once (for its goal) and every `attempt_finished` once, and adds
each run's cost to its task's goal. The planner's own model calls (drafting, titling, sizing) are not in
either total: the Performance page has them.
"""

import time
from decimal import Decimal
from typing import Any

from meta_harness.planner import cost_facts

EVENTS_PER_READ = 5000
# How long one reading of every goal's cost is kept: the list is read on each page load and each scroll.
CACHE_SECONDS = 30.0


def by_goal(tasks: cost_facts.TaskPages, events: cost_facts.EventReader) -> dict[str, Decimal]:
    """Each goal's cost: what its tasks' runs spent, by goal id. A goal with no runs is not in it."""
    goal_of = {task["id"]: task.get("goal_id") for task in cost_facts.every_task(tasks)}
    costs: dict[str, Decimal] = {}
    position = 0
    while True:
        page = events.read(from_position=position, limit=EVENTS_PER_READ, event_type="attempt_finished")
        found = page.get("events") or []
        for event in found:
            goal_id = goal_of.get(event.get("task_id") or "")
            if goal_id:
                spent = Decimal(str((event.get("payload") or {}).get("spent_usd") or 0))
                costs[goal_id] = costs.get(goal_id, Decimal(0)) + spent
        if len(found) < EVENTS_PER_READ:
            return costs
        position = max(e["position"] for e in found)


class Cache:
    """The last reading of every goal's cost, kept CACHE_SECONDS. A reading that fails is not kept, and says
    why, so the next page load tries again."""

    def __init__(self, tasks: Any, events: Any, seconds: float = CACHE_SECONDS) -> None:
        self.tasks = tasks
        self.events = events
        self.seconds = seconds
        self._read: tuple[float, dict[str, Decimal]] | None = None

    def get(self) -> tuple[dict[str, Decimal] | None, str]:
        """Every goal's cost and "", or None and why it could not be read."""
        if self._read and time.monotonic() - self._read[0] < self.seconds:
            return self._read[1], ""
        try:
            costs = by_goal(self.tasks, self.events)
        except Exception as err:  # noqa: BLE001 - the goal list still shows, with each cost unknown and why
            return None, f"{type(err).__name__}: {err}"[:300]
        self._read = (time.monotonic(), costs)
        return costs, ""


def give(goals: list[dict], costs: dict[str, Decimal] | None, why: str) -> None:
    """Put each goal's cost on it: `cost_usd` (a string, "0" for a goal with no runs), or None with
    `cost_unknown` saying why."""
    for goal in goals:
        if costs is None:
            goal["cost_usd"], goal["cost_unknown"] = None, why or "the cost could not be read"
        else:
            goal["cost_usd"], goal["cost_unknown"] = str(costs.get(goal["id"], Decimal(0))), None
