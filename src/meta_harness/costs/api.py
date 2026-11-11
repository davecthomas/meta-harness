"""The cost service's API and its harvest loop.

    GET  /v1/costs            costs over time: buckets, a series per value of a dimension, totals and the trend
    POST /v1/costs/harvest    harvest every source now (admins)

A cost row is recorded by task, so the task's own facts that change after it spent, its issue's labels and
its pull request's outcome, are not on the row: the planner gives them when costs are asked for (the facts
callable), and a label or outcome filter is the tasks that have it. A task whose issue carries several labels
counts its cost under each in an even share in a breakdown by label, so the series still add up to the total.

Costs are gathered from their sources into one table (store.py) by a harvest every MH_COSTS_HARVEST_SECONDS:
model cost from the event service (model_source.py) and cloud cost from the billing export (cloud_source.py).
A source is a module with a harvest function, so another is added without changing the rest.

Who sees what is decided here, from the role the edge names, not by the console: an admin sees every source
and every goal; anyone else sees only the model cost of their own work, the tasks of the goals they created.

The routes are registered on the planner's app and the loop runs in its process, as the configuration API
does, until the cost service has a process of its own.
"""

import asyncio
import logging
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from meta_harness import db
from meta_harness.costs import cloud_source, model_source, series, store

log = logging.getLogger("meta_harness.costs")
HARVEST_SETTING = "MH_COSTS_HARVEST_SECONDS"
DEFAULT_HARVEST_SECONDS = 300.0
MAX_DAYS = 400
TOP_TASKS = 10
# Below this, what was spent in the span before is too little for a percentage change to mean anything: from
# a cent to $100 is not a "1,000,000% rise", it is new spending.
MIN_COMPARABLE_USD = 1.0
# The filters a request may give, each a comma-separated list of values.
FILTERS = ("source", "kind", "goal", "task", "repo", "model", "engine", "operator")
# The filters and breakdowns that are a task's facts, not a cost row's columns.
TASK_FACTS = ("label", "outcome")
# A task's pull request outcome: merged, closed without merging, still open, or none opened.
OUTCOMES = ("merged", "closed", "open", "none")


def harvest_seconds() -> float:
    """How often the sources are harvested; 0 turns the loop off.

    Raises:
        RuntimeError: The setting is not a number, 0 or more.
    """
    raw = os.environ.get(HARVEST_SETTING, "").strip()
    if not raw:
        return DEFAULT_HARVEST_SECONDS
    try:
        seconds = float(raw)
    except ValueError:
        seconds = -1
    if seconds < 0:
        raise RuntimeError(f"{HARVEST_SETTING} must be a number of seconds, 0 or more, not {raw!r}")
    return seconds


def check_settings() -> None:
    """At startup: a bad cost setting stops the service with its name."""
    harvest_seconds()
    try:
        cloud_source.table()
    except cloud_source.CloudCostError as err:
        raise RuntimeError(str(err)) from err


def harvest_all(events: Any, tasks: Any, goal: Callable[[str], dict | None]) -> dict[str, Any]:
    """Harvest every source once. A source that fails is recorded with why, and the others still run."""
    found: dict[str, Any] = {}
    cache: dict[str, dict | None] = {}

    def task(task_id: str) -> dict | None:
        if task_id and task_id not in cache:
            try:
                cache[task_id] = tasks.get(task_id)
            except Exception:  # noqa: BLE001 - a task that is gone still cost what it cost
                cache[task_id] = None
        return cache.get(task_id)

    with db.connect(autocommit=True) as conn:
        for source, run in (
            (model_source.SOURCE, lambda: model_source.harvest(conn, events, task, goal)),
            (cloud_source.SOURCE, lambda: cloud_source.harvest(conn, datetime.now(UTC).date())),
        ):
            try:
                found[source] = run()
            except Exception as err:  # noqa: BLE001 - one source's failure is its own
                log.warning("could not harvest %s costs: %s", source, err)
                store.failed(conn, source, str(err))
                found[source] = f"failed: {err}"
    return found


def sources(conn: Any) -> dict[str, dict]:
    """Each cost source, for the page to say what it is showing and what it is not."""
    harvested = store.harvests(conn)
    configured = bool(os.environ.get(cloud_source.SETTING, "").strip())
    return {
        "model": {
            "configured": True,
            **harvested.get("model", {}),
            "note": "What model calls cost, from each run's grant.",
        },
        "cloud": {
            "configured": configured,
            **harvested.get("cloud", {}),
            "note": "The Google Cloud bill, by service."
            if configured
            else f"Not connected: turn on the billing export to BigQuery and set {cloud_source.SETTING}.",
        },
        "temporal": {
            "configured": False,
            "note": "Temporal runs on the VM, so its cost is inside Compute Engine's, under cloud.",
        },
    }


def _matches(fact: dict, wanted: dict[str, list[str]]) -> bool:
    """Whether a task's facts meet every fact filter: one of the labels asked for, and one of the outcomes."""
    if "label" in wanted and not set(fact.get("labels") or []) & set(wanted["label"]):
        return False
    return "outcome" not in wanted or fact.get("outcome") in wanted["outcome"]


def by_fact(rows: list[tuple[date, str, Any]], facts: dict[str, dict], name: str) -> list[tuple[date, str, Any]]:
    """Each day's cost per task, (day, task id, cost), as each day's cost per label or outcome. A task with
    several labels puts an even share under each; a cost of no task, or of a task with none, is under ''."""
    found = []
    for day, task_id, usd in rows:
        fact = facts.get(task_id) or {}
        keys = (fact.get("labels") or [""]) if name == "label" else [fact.get("outcome") or ""]
        found.extend((day, key, usd / len(keys)) for key in keys)
    return found


def register(
    app: FastAPI,
    operator: Callable[..., str],
    admin: Callable[..., bool],
    events: Any,
    tasks: Any,
    goal: Callable[[str], dict | None],
    facts: Callable[[], dict[str, dict]] = dict,
) -> Callable[[], Any]:
    """Adds the cost routes. admin answers whether the caller is one. facts gives each task's issue labels and
    pull request outcome, {task id: {"labels": [...], "outcome": ...}}. Returns the harvest loop to start."""

    def harvest_now() -> dict[str, Any]:
        return harvest_all(events, tasks, goal)

    async def harvest_forever() -> None:
        every = harvest_seconds()
        while every > 0:
            try:
                await asyncio.to_thread(harvest_now)
            except Exception as err:  # noqa: BLE001 - one failed harvest must not stop the next
                log.warning("cost harvest failed: %s", err)
            await asyncio.sleep(every)

    @app.post("/v1/costs/harvest")
    def harvest(who: str = Depends(operator), is_admin: bool = Depends(admin)) -> dict:
        if not is_admin:
            raise HTTPException(status_code=403, detail=f"{who} is not an admin")
        return {"harvested": harvest_now()}

    @app.get("/v1/costs")
    def costs(
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        bucket: str = Query("day", pattern="^(day|week|month)$"),
        group_by: str = Query("source", pattern="^(source|kind|goal|task|repo|model|engine|operator|label|outcome)$"),
        source: str | None = None,
        kind: str | None = None,
        goal_id: str | None = Query(default=None, alias="goal"),
        task: str | None = None,
        repo: str | None = None,
        model: str | None = None,
        engine: str | None = None,
        operator_is: str | None = Query(default=None, alias="operator"),
        label: str | None = None,
        outcome: str | None = None,
        who: str = Depends(operator),
        is_admin: bool = Depends(admin),
    ) -> dict:
        """Costs from one day to another, in buckets, as one series per value of group_by, within the filters:
        the buckets, the series most expensive first, the totals, the change from the same number of days
        before (none when too little was spent then to compare), the linear trend, the most expensive tasks,
        and what each source is. An admin sees everything; anyone else, the
        model cost of their own work. label and outcome keep the costs of the tasks whose issue carries one
        of the labels, or whose pull request has one of the outcomes."""
        last = end or datetime.now(UTC).date()
        first = start or last - timedelta(days=29)
        if first > last or (last - first).days > MAX_DAYS:
            raise HTTPException(status_code=422, detail=f"give a span of 1 to {MAX_DAYS} days")
        given = {
            "source": source,
            "kind": kind,
            "goal": goal_id,
            "task": task,
            "repo": repo,
            "model": model,
            "engine": engine,
            "operator": operator_is,
        }
        filters = {name: [v for v in value.split(",") if v] for name, value in given.items() if value}
        if not is_admin:
            # Their own AI work, and nothing of the deployment's other costs.
            filters["source"] = ["model"]
            filters["operator"] = [who]
        wanted = {
            name: [v for v in value.split(",") if v]
            for name, value in (("label", label), ("outcome", outcome))
            if value
        }
        if unknown := set(wanted.get("outcome", [])) - set(OUTCOMES):
            raise HTTPException(status_code=422, detail=f"outcome is one of {', '.join(OUTCOMES)}, not {unknown}")
        needed = bool(wanted) or group_by in TASK_FACTS
        known: dict[str, dict] = {}
        # The cloud bill is no task's: it has no label and no outcome to offer.
        if needed or filters.get("source") != ["cloud"]:
            try:
                known = facts()
            except Exception as err:  # noqa: BLE001 - without them only these filters and breakdowns fail
                if needed:
                    raise HTTPException(status_code=502, detail=f"could not read the tasks: {err}") from err
                log.warning("could not read the tasks' labels and outcomes: %s", err)
        if wanted:
            matching = {task_id for task_id, fact in known.items() if _matches(fact, wanted)}
            if "task" in filters:
                matching &= set(filters["task"])
            filters["task"] = sorted(matching)
        days = (last - first).days + 1
        with db.connect(autocommit=True) as conn:
            if group_by in TASK_FACTS:
                rows = by_fact(store.by_day(conn, first, last, "task", filters), known, group_by)
            else:
                rows = store.by_day(conn, first, last, group_by, filters)
            built = series.build(rows, first, last, bucket)
            total = float(store.total(conn, first, last, filters))
            before = float(store.total(conn, first - timedelta(days=days), first - timedelta(days=1), filters))
            costliest = store.top(conn, first, last, "task", filters, TOP_TASKS)
            choices = {name: store.values(conn, name, filters) for name in ("source", "kind", "goal", "repo", "model")}
            spent = [known[t] for t in store.values(conn, "task", filters) if t in known] if known else []
            described = sources(conn) if is_admin else {"model": sources(conn)["model"]}
        # Within the source and operator in view, never narrowed by their own filter, as the others are.
        choices["label"] = sorted({name for fact in spent for name in fact.get("labels") or []})
        choices["outcome"] = [o for o in OUTCOMES if any(fact.get("outcome") == o for fact in spent)]
        labels: dict[str, str] = {}
        for key in (
            {s["key"] for s in built["series"]} | set(choices["goal"]) if group_by == "goal" else set(choices["goal"])
        ):
            found = goal(key) if key.startswith("g-") else None
            if found:
                labels[key] = found.get("title") or found["statement"][:80]
        top_tasks = []
        for task_id, usd in costliest:
            try:
                title = tasks.get(task_id).get("title")
            except Exception:  # noqa: BLE001 - a task that is gone is still named by its id
                title = None
            top_tasks.append({"task_id": task_id, "title": title, "usd": round(float(usd), 4)})
        trend = built["trend"]
        per_bucket = {"day": 1, "week": 7, "month": 30}[bucket]
        return {
            "from": first.isoformat(),
            "to": last.isoformat(),
            "bucket": bucket,
            "group_by": group_by,
            **built,
            "total": round(total, 4),
            "daily_average": round(total / days, 4),
            "previous_total": round(before, 4),
            "change_percent": round(100 * (total - before) / before, 1) if before >= MIN_COMPARABLE_USD else None,
            # A month at the rate the trend line ends on.
            "projected_month": round(max(trend["end"], 0) * 30 / per_bucket, 2) if trend else None,
            "top_tasks": top_tasks,
            "labels": labels,
            "choices": choices,
            "sources": described,
            "scope": "all" if is_admin else "own",
        }

    return harvest_forever
