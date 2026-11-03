"""Starts one RunTask workflow, waits for it, and prints the result.

    python -m run_task.starter --harness-url URL [--ref REF] [--task TEXT]
                               [--engine E] [--model M] [--workspace DIR] [--follow]

--ref is the caller's name for the task. The workflow id is run-task-<ref>,
and Temporal refuses a second task with the same ref. --follow prints the
run's events from the harness server while the task runs.

Exit codes: 0 completed, 1 failed, 3 canceled, 130 interrupted.
Ctrl-C cancels the workflow before the starter exits.

Environment: HARNESS_SERVER_TOKEN (for --follow), TEMPORAL_ADDRESS (default
localhost:7233), TEMPORAL_UI_URL (default http://localhost:8233).
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

from run_task import harness_client
from run_task.shared import (
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
    request = RunTaskInput(engine=args.engine, model=args.model, harness_url=args.harness_url, task=args.task)
    try:
        handle = await client.start_workflow(
            RunTask.run,
            request,
            id=workflow_id,
            task_queue=TASK_QUEUE,
            execution_timeout=timedelta(minutes=30),
            # One ref names one task, as external_ref does in the design.
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            memo={"ref": args.ref, "harness_url": args.harness_url, "workspace": args.workspace},
        )
    except WorkflowAlreadyStartedError:
        print(f"[starter] a task with REF {args.ref} already exists on this Temporal server. Use another REF.")
        return 1
    print(f"[starter] started workflow {workflow_id}  engine={request.engine}  model={request.model}", flush=True)
    print(f"[starter] task: {request.task or f'prompts/{request.template}.txt'}", flush=True)
    print(f"[starter] web UI: {UI_URL}/namespaces/{TEMPORAL_NAMESPACE}/workflows/{workflow_id}", flush=True)

    follow: asyncio.Task | None = None
    if args.follow:
        token = harness_client.server_token() or ""
        run_id = f"{workflow_id}:run_harness"
        follow = asyncio.create_task(harness_client.follow(args.harness_url, token, run_id, f"[{args.ref}]"))

    try:
        result: RunTaskResult = await handle.result()
    except WorkflowFailureError as err:
        await _stop_following(follow)
        await _print_history(handle)
        if isinstance(err.cause, CancelledError):
            print(f"[starter] workflow {workflow_id} canceled", flush=True)
            return EXIT_CANCELED
        print(f"[starter] workflow {workflow_id} failed: {err.cause}", flush=True)
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
    print(f"[result] files changed: {', '.join(h.files_changed) or 'none'}", flush=True)
    if args.workspace:
        print(f"[result] workspace: {args.workspace}", flush=True)
    print(f"[result] answer: {h.answer}", flush=True)
    return 0


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
    parser.add_argument("--harness-url", required=True, help="the harness server that runs this task")
    parser.add_argument("--ref", default=uuid.uuid4().hex[:8], help="your name for the task (default: random)")
    parser.add_argument("--task", default="", help="the task text (default: prompts/hello-v1.txt)")
    parser.add_argument("--engine", default="claude")
    parser.add_argument("--model", default="claude-haiku-4-5")
    parser.add_argument("--workspace", default="", help="the harness server's workspace, printed with the result")
    parser.add_argument("--follow", action="store_true", help="print the run's events while it runs")
    try:
        sys.exit(asyncio.run(main(parser.parse_args())))
    except KeyboardInterrupt:
        sys.exit(130)
