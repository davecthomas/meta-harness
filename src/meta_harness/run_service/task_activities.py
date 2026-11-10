"""The activities that tie a RunTask workflow to its task in the task service.

load_task reads the task from the Task API, set_task_status moves it, and the
record_* activities write the meta-harness run service's run and
routing_decision rows and append task_routed, attempt_finished and
pull_request_opened to the event service. They run only for a task submitted
through the Task API; a run started with a REF and task text skips them.

The Task API client and Postgres calls block, so each runs in a thread.

Environment: MH_TASKS_URL and MH_SERVICE_KEY (a key from the task service's
MH_SERVICE_KEYS), MH_DATABASE_URL, MH_EVENTS_URL.
"""

import asyncio
import os
import re
from decimal import Decimal

from psycopg.types.json import Jsonb
from temporalio import activity
from temporalio.exceptions import ApplicationError

from meta_harness import db
from meta_harness.run_service import events
from meta_harness.run_service.shared import (
    DEFAULT_SPEND_LIMIT_USD,
    AttemptRecord,
    PublishedRecord,
    RouteRecord,
    TaskSpec,
    TaskStatusRequest,
)
from meta_harness.tasks.client import TaskApiError, TaskClient

# complexity on a task becomes the router's floor.
FLOOR_FOR_COMPLEXITY = {"high": "complex"}
GITHUB_REF = re.compile(r"^github:(?P<repo>[\w.-]+/[\w.-]+)#(?P<number>\d+)$")


def _tasks() -> TaskClient:
    key = os.environ.get("MH_SERVICE_KEY")
    if not key:
        raise ApplicationError(
            "set MH_SERVICE_KEY in the worker's environment", type="HarnessConfigError", non_retryable=True
        )
    return TaskClient(api_key=key)


def _task_api_error(err: TaskApiError) -> ApplicationError:
    # A refusal is the request's fault; anything else may pass on a retry.
    return ApplicationError(str(err), type="TaskApiError", non_retryable=err.status in (401, 403, 404, 409, 422))


@activity.defn
async def load_task(task_id: str) -> TaskSpec:
    try:
        task = await asyncio.to_thread(lambda: _tasks().get(task_id))
    except TaskApiError as err:
        raise _task_api_error(err) from err
    match = GITHUB_REF.match(task["external_ref"])
    return TaskSpec(
        task_id=task["id"],
        status=task["status"],
        repo=task["repo"],
        title=task["title"],
        description=task["description"],
        checks=list(task["acceptance_checks"]),
        pin=task["pin"] or "",
        floor=FLOOR_FOR_COMPLEXITY.get(task["complexity"] or "", ""),
        ceiling=task["max_tier"] or "",
        spend_limit_usd=task["max_usd"] or DEFAULT_SPEND_LIMIT_USD,
        closes=f"{match['repo']}#{match['number']}" if match else "",
        goal_id=task.get("goal_id") or "",
        attempts=int(task.get("attempts") or 1),
    )


@activity.defn
async def set_task_status(request: TaskStatusRequest) -> str:
    """Moves the task. Returns the status it has now; a repeat of the same move is a no-op."""
    fields = {"pr_url": request.pr_url} if request.pr_url else {}
    try:
        task = await asyncio.to_thread(
            lambda: _tasks().set_status(request.task_id, request.status, request.reason or None, **fields)
        )
    except TaskApiError as err:
        raise _task_api_error(err) from err
    return task["status"]


def _insert_route(record: RouteRecord, labels: list[str]) -> None:
    route = record.route
    with db.connect(autocommit=True) as conn, conn.transaction():
        conn.execute(
            "INSERT INTO run_service.routing_decision (task_id, route_no, route_tier, route_method, route_confidence, "
            "candidates, route_floor, route_ceiling, classifier_usd, config_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (task_id, route_no) DO NOTHING",
            (
                record.task_id,
                record.route_no,
                route.tier,
                route.method,
                route.confidence,
                Jsonb(labels),
                record.floor or None,
                record.ceiling or None,
                Decimal(route.classifier_usd),
                route.config_id,
            ),
        )


@activity.defn
async def record_route(record: RouteRecord) -> None:
    route = record.route
    labels = [c.label for c in route.candidates]
    await asyncio.to_thread(_insert_route, record, labels)
    events.emit(
        "task_routed",
        {
            "route_no": record.route_no,
            "tier": route.tier,
            "method": route.method,
            "confidence": route.confidence,
            "candidates": labels,
            "floor": record.floor or None,
            "ceiling": record.ceiling or None,
            "classifier_usd": route.classifier_usd,
        },
        task_id=record.task_id,
        # One event per route call, however often Temporal retries this activity.
        event_id=events.stable_id(record.task_id, "task_routed", record.route_no),
    )


def _insert_run(record: AttemptRecord) -> None:
    a, h = record.attempt, record.harness
    with db.connect(autocommit=True) as conn, conn.transaction():
        conn.execute(
            "INSERT INTO run_service.run (id, task_id, attempt_no, route_tier, candidate, harness_profile, model, "
            "decision, detail, status, end_reason, turns, tokens_in, tokens_out, spent_usd, files_changed) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (
                a.run_id,
                record.task_id,
                a.attempt,
                a.tier,
                a.candidate,
                record.candidate.profile_name,
                f"{record.candidate.engine}:{record.candidate.model}",
                a.decision,
                a.detail,
                h.status if h else None,
                h.end_reason if h else None,
                h.turns if h else None,
                h.tokens_in if h else None,
                h.tokens_out if h else None,
                Decimal(a.spent_usd),
                Jsonb(h.files_changed if h else []),
            ),
        )


@activity.defn
async def record_attempt(record: AttemptRecord) -> None:
    a, h = record.attempt, record.harness
    await asyncio.to_thread(_insert_run, record)
    events.emit(
        "attempt_finished",
        {
            "attempt": a.attempt,
            "tier": a.tier,
            "candidate": a.candidate,
            "decision": a.decision,
            "detail": a.detail,
            "spent_usd": a.spent_usd,
            "status": h.status if h else None,
            "end_reason": h.end_reason if h else None,
        },
        task_id=record.task_id,
        run_id=a.run_id,
        event_id=events.stable_id(a.run_id, "attempt_finished"),
    )


@activity.defn
async def record_published(record: PublishedRecord) -> None:
    """Saves the pull request on the task as soon as it opens, and records pull_request_opened.

    The task stays running through its review loop, so without this its record would show no pull
    request until the task ended, and the console could not list it.
    """
    p = record.published
    try:
        await asyncio.to_thread(
            lambda: _tasks().set_status(
                record.task_id,
                "running",
                "its pull request is open, in the review loop",
                pr_url=p.pr_url,
                head_sha=p.commit,
            )
        )
    except TaskApiError as err:
        raise _task_api_error(err) from err
    events.emit(
        "pull_request_opened",
        {"pr_url": p.pr_url, "branch": p.branch, "files": p.files},
        task_id=record.task_id,
        event_id=events.stable_id(record.task_id, "pull_request_opened", p.commit),
    )
