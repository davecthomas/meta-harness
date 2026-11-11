"""The cost service's API and its harvest loop.

    GET  /v1/costs            costs over time: buckets, a series per value of a dimension, totals and the trend
    POST /v1/costs/harvest    harvest every source now (admins)

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
# The filters a request may give, each a comma-separated list of values.
FILTERS = ("source", "kind", "goal", "task", "repo", "model", "engine", "operator")


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


def register(
    app: FastAPI,
    operator: Callable[..., str],
    admin: Callable[..., bool],
    events: Any,
    tasks: Any,
    goal: Callable[[str], dict | None],
) -> Callable[[], Any]:
    """Adds the cost routes. admin answers whether the caller is one. Returns the harvest loop to start."""

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
        group_by: str = Query("source", pattern="^(source|kind|goal|task|repo|model|engine|operator)$"),
        source: str | None = None,
        kind: str | None = None,
        goal_id: str | None = Query(default=None, alias="goal"),
        task: str | None = None,
        repo: str | None = None,
        model: str | None = None,
        engine: str | None = None,
        operator_is: str | None = Query(default=None, alias="operator"),
        who: str = Depends(operator),
        is_admin: bool = Depends(admin),
    ) -> dict:
        """Costs from one day to another, in buckets, as one series per value of group_by, within the filters:
        the buckets, the series dearest first, the totals, the change against the same span before, the
        linear trend, the dearest tasks, and what each source is. An admin sees everything; anyone else, the
        model cost of their own work."""
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
        days = (last - first).days + 1
        with db.connect(autocommit=True) as conn:
            built = series.build(store.by_day(conn, first, last, group_by, filters), first, last, bucket)
            total = float(store.total(conn, first, last, filters))
            before = float(store.total(conn, first - timedelta(days=days), first - timedelta(days=1), filters))
            dearest = store.top(conn, first, last, "task", filters, TOP_TASKS)
            choices = {name: store.values(conn, name, filters) for name in ("source", "kind", "goal", "repo", "model")}
            described = sources(conn) if is_admin else {"model": sources(conn)["model"]}
        labels: dict[str, str] = {}
        for key in (
            {s["key"] for s in built["series"]} | set(choices["goal"]) if group_by == "goal" else set(choices["goal"])
        ):
            found = goal(key) if key.startswith("g-") else None
            if found:
                labels[key] = found.get("title") or found["statement"][:80]
        top_tasks = []
        for task_id, usd in dearest:
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
            "change_percent": round(100 * (total - before) / before, 1) if before else None,
            # A month at the rate the trend line ends on.
            "projected_month": round(max(trend["end"], 0) * 30 / per_bucket, 2) if trend else None,
            "top_tasks": top_tasks,
            "labels": labels,
            "choices": choices,
            "sources": described,
            "scope": "all" if is_admin else "own",
        }

    return harvest_forever
