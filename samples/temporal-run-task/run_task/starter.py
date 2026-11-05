"""Starts one RunTask workflow, waits for it, and prints the result.

    python -m run_task.starter [--ref REF] [--task TEXT] [--pin PAIR]
                               [--floor TIER] [--ceiling TIER] [--spend-limit USD] [--follow]

--ref is the caller's name for the task. The workflow id is run-task-<ref>,
and Temporal refuses a second task with the same ref. The ai-api-unified
router picks the task's tier and candidates from config/models.json, and
--floor and --ceiling bound the tier. --pin runs one harness-profile-and-model
pair, harness:<profile>/<engine>:<model>, with no routing. --spend-limit
covers all the task's runs. --follow prints each run's events from the
harness server in its per-run sandbox while the task runs. pi's workspace is
runs/<ref>/workspace, shared by the task's runs.

Exit codes: 0 completed, 1 failed, 3 canceled, 130 interrupted.
Ctrl-C cancels the workflow before the starter exits.

Environment: TEMPORAL_ADDRESS (default localhost:7233), TEMPORAL_UI_URL
(default http://localhost:8233), RUNS_DIR (default runs/ in this sample).
"""

import argparse
import asyncio
import contextlib
import os
import sys
import uuid
from datetime import timedelta

from temporalio.api.enums.v1 import EventType
from temporalio.api.history.v1 import HistoryEvent
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import CancelledError, WorkflowAlreadyStartedError

from run_task import harness_client, sandbox
from run_task.shared import (
    DEFAULT_SPEND_LIMIT_USD,
    TASK_QUEUE,
    TEMPORAL_ADDRESS,
    TEMPORAL_NAMESPACE,
    RunTaskInput,
    RunTaskResult,
    workflow_id_for,
)
from run_task.workflows import RunTask

UI_URL = os.environ.get("TEMPORAL_UI_URL", "http://localhost:8233")
EXIT_CANCELED = 3


async def main(args: argparse.Namespace) -> int:
    client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)
    workflow_id = workflow_id_for(args.ref)
    request = RunTaskInput(
        task=args.task,
        spend_limit_usd=args.spend_limit,
        pin=args.pin,
        floor=args.floor,
        ceiling=args.ceiling,
    )
    try:
        handle = await client.start_workflow(
            RunTask.run,
            request,
            id=workflow_id,
            task_queue=TASK_QUEUE,
            execution_timeout=timedelta(minutes=30),
            # One ref names one task, as external_ref does in the design.
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            memo={"ref": args.ref, "workspace": str(sandbox.workspace_for(args.ref))},
        )
    except WorkflowAlreadyStartedError:
        print(f"[starter] a task with REF {args.ref} already exists on this Temporal server. Use another REF.")
        return 1
    routing = f"pin={request.pin}" if request.pin else f"floor={request.floor or '-'}  ceiling={request.ceiling or '-'}"
    print(
        f"[starter] started workflow {workflow_id}  {routing}  spend limit=${request.spend_limit_usd}",
        flush=True,
    )
    print(f"[starter] task: {request.task or f'prompts/{request.template}.txt'}", flush=True)
    print(f"[starter] web UI: {UI_URL}/namespaces/{TEMPORAL_NAMESPACE}/workflows/{workflow_id}", flush=True)

    follow: asyncio.Task | None = None
    if args.follow:
        follow = asyncio.create_task(harness_client.follow_task(handle, f"[{args.ref}]"))

    try:
        result: RunTaskResult = await handle.result()
    except WorkflowFailureError as err:
        await _stop_following(follow)
        await _print_history(handle)
        if isinstance(err.cause, CancelledError):
            print(f"[starter] workflow {workflow_id} canceled", flush=True)
            return EXIT_CANCELED
        print(f"[starter] workflow {workflow_id} failed: {err.cause}", flush=True)
        details = getattr(err.cause, "details", None) or ()
        for attempt in details[0] if details and isinstance(details[0], list) else []:
            _print_attempt(attempt)
        return 1
    except asyncio.CancelledError:
        # Ctrl-C: cancel the workflow and wait for it to end while the worker is still up,
        # so it does not stay open with no worker to finish it.
        task = asyncio.current_task()
        if task:
            task.uncancel()
        print(f"[starter] interrupted, canceling workflow {workflow_id}", flush=True)
        await handle.cancel()
        try:
            await asyncio.wait_for(handle.result(), timeout=30)
        except WorkflowFailureError:
            print(f"[starter] workflow {workflow_id} canceled", flush=True)
        except TimeoutError:
            print(f"[starter] workflow {workflow_id} did not end within 30s", flush=True)
        raise KeyboardInterrupt from None

    await _stop_following(follow)
    await _print_history(handle)
    h = result.harness
    print(f"[starter] workflow {workflow_id} completed", flush=True)
    if result.route:
        r = result.route
        confidence = f"  confidence={r.confidence:.2f}" if r.confidence is not None else ""
        print(
            f"[result] route: tier={r.tier}  method={r.method}{confidence}  classifier usd={r.classifier_usd}",
            flush=True,
        )
    for attempt in result.attempts:
        _print_attempt(vars(attempt))
    print(f"[result] task spent ${result.spent_usd} of its ${request.spend_limit_usd} limit", flush=True)
    print(
        f"[result] template={result.template}  run={h.run_id}  status={h.status} ({h.end_reason})  "
        f"attempt={h.attempt}  reattached={h.reattached}",
        flush=True,
    )
    print(
        f"[result] turns={h.turns}  failed calls={h.failed_calls}  tokens in={h.tokens_in} out={h.tokens_out}  "
        f"usd={h.usd:.6f}",
        flush=True,
    )
    print(
        f"[result] run grant: the HTTP Gateway counted ${h.grant_spent_usd or '?'} of the ${h.spend_limit_usd} limit",
        flush=True,
    )
    print(f"[result] files changed: {', '.join(h.files_changed) or 'none'}", flush=True)
    print(f"[result] workspace: {sandbox.workspace_for(args.ref)}", flush=True)
    print(f"[result] harness server log: {sandbox.log_path_for(args.ref, h.run_id)}", flush=True)
    print(f"[result] answer: {h.answer}", flush=True)
    return 0


def _print_attempt(attempt: dict) -> None:
    print(
        f"[result] run {attempt['attempt']}: {attempt['candidate']} (tier {attempt['tier'] or 'pinned'})  "
        f"{attempt['decision']}  spent ${attempt['spent_usd']}  {attempt['detail']}",
        flush=True,
    )


async def _stop_following(follow: asyncio.Task | None) -> None:
    """Gives the event printer a moment to print run_finished, then stops it."""
    if follow is None:
        return
    with contextlib.suppress(TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(follow, timeout=3)


async def _print_history(handle: WorkflowHandle) -> None:
    """Prints the events the Temporal server recorded for the run, one line each.

    This is the record a worker replays to rebuild the workflow. Heartbeats are
    not events, so they do not appear. A retried activity shows one start
    event, for its last attempt.
    """
    activity_names: dict[int, str] = {}
    print(f"[history] events the Temporal server recorded for {handle.id}:", flush=True)
    async for event in handle.fetch_history_events():
        kind = "".join(part.capitalize() for part in EventType.Name(event.event_type).split("_")[2:])
        detail = _event_detail(event, activity_names)
        print(f"[history] {event.event_id:>3}  {kind:<34}{detail}".rstrip(), flush=True)


def _event_detail(event: HistoryEvent, activity_names: dict[int, str]) -> str:
    """The parts of an event worth a glance: the activity name, the attempt, a failure message."""
    field = event.WhichOneof("attributes")
    attrs = getattr(event, field) if field else None
    if attrs is None:
        return ""
    if field == "workflow_execution_started_event_attributes":
        return f"task queue {attrs.task_queue.name}"
    if field == "activity_task_scheduled_event_attributes":
        activity_names[event.event_id] = attrs.activity_type.name
        return attrs.activity_type.name
    if not hasattr(attrs, "scheduled_event_id") or not field.startswith("activity_task_"):
        return ""
    parts = [activity_names.get(attrs.scheduled_event_id, "")]
    if field == "activity_task_started_event_attributes":
        parts.append(f"attempt {attrs.attempt}")
        if attrs.HasField("last_failure"):
            parts.append(f"previous attempt: {attrs.last_failure.message}")
    if field in ("activity_task_failed_event_attributes", "activity_task_timed_out_event_attributes"):
        parts.append(attrs.failure.message)
    return ", ".join(part for part in parts if part)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", default=uuid.uuid4().hex[:8], help="your name for the task (default: random)")
    parser.add_argument("--task", default="", help="the task text (default: prompts/hello-v1.txt)")
    parser.add_argument("--pin", default="", help="run one pair, harness:<profile>/<engine>:<model>, with no routing")
    parser.add_argument("--floor", default="", help="the lowest router tier the task may take")
    parser.add_argument("--ceiling", default="", help="the highest router tier the task may take")
    parser.add_argument(
        "--spend-limit",
        default=DEFAULT_SPEND_LIMIT_USD,
        help=f"most the task may spend on model calls across its runs, in USD (default {DEFAULT_SPEND_LIMIT_USD})",
    )
    parser.add_argument("--follow", action="store_true", help="print the run's events while it runs")
    try:
        sys.exit(asyncio.run(main(parser.parse_args())))
    except KeyboardInterrupt:
        sys.exit(130)
