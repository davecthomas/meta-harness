"""Looks at tasks the starter started: list them, show one, watch one, cancel one.

    python -m meta_harness.run_service.tasks list
    python -m meta_harness.run_service.tasks status REF
    python -m meta_harness.run_service.tasks watch REF
    python -m meta_harness.run_service.tasks cancel REF

REF is the name given to `make task` or `make run`. A task makes one run per
tier candidate it tries. These commands ask the RunTask workflow which run it
is on, and find that run's per-run sandbox container by its run id. The
container is removed when the run ends, so `watch` and the live part of
`status` work only while the task runs.

Environment: GATEWAY_URL and GATEWAY_API_KEY (for the run grant's spend),
TEMPORAL_ADDRESS (default localhost:7233).
"""

import argparse
import asyncio
import sys
from typing import Any

import httpx
from temporalio.client import (
    Client,
    WorkflowExecutionStatus,
    WorkflowFailureError,
    WorkflowHandle,
    WorkflowQueryFailedError,
)
from temporalio.exceptions import CancelledError
from temporalio.service import RPCError

from meta_harness.run_service import harness_client, run_grants, sandbox
from meta_harness.run_service.shared import TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE, RunTaskResult, workflow_id_for
from meta_harness.run_service.workflows import RunTask


async def list_tasks(client: Client) -> int:
    print(f"{'REF':<24}{'STATUS':<12}{'STARTED':<22}WORKSPACE")
    async for execution in client.list_workflows("WorkflowType = 'RunTask'", limit=50):
        memo = await execution.memo()
        status = execution.status.name if execution.status else "UNKNOWN"
        started = execution.start_time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"{memo.get('ref', execution.id):<24}{status:<12}{started:<22}{memo.get('workspace', '')}")
    return 0


async def status(client: Client, ref: str) -> int:
    handle = _handle(client, ref)
    described = await handle.describe()
    memo = await described.memo()
    state = described.status or WorkflowExecutionStatus.RUNNING
    print(f"[status] {ref}: workflow {handle.id} {state.name}, started {described.start_time:%Y-%m-%d %H:%M:%S}")
    if memo.get("workspace"):
        print(f"[status] workspace: {memo['workspace']}")
    if state == WorkflowExecutionStatus.RUNNING:
        run_id = await _current_run_id(handle)
        run = await _harness_get("/status", run_id)
        usage = await _harness_get("/usage", run_id)
        if run and usage:
            print(
                f"[status] run {run['run_id']}: {run['status']}  turns={run['turns']}  "
                f"calls={usage['calls']} failed={usage['failed_calls']}  "
                f"tokens in={usage['input_tokens']} out={usage['output_tokens']}  usd={usage['usd']:.6f}"
            )
        else:
            print("[status] the harness server has not started the run yet")
        grant = await run_grants.get(run_id) if run_id else None
        if grant:
            print(
                f"[status] run grant: {grant['engine']}:{grant['model']}  spent ${grant['spent_usd']} "
                f"of ${grant['spend_limit_usd']}  expires {grant['expires_at']}"
            )
        else:
            print("[status] run grant: none live on the HTTP Gateway")
        return 0
    return await _print_outcome(handle, ref)


async def watch(client: Client, ref: str) -> int:
    handle = _handle(client, ref)
    await harness_client.follow_task(handle, f"[{ref}]")
    return await _print_outcome(handle, ref)


async def cancel(client: Client, ref: str) -> int:
    handle = _handle(client, ref)
    print(f"[cancel] canceling workflow {handle.id}")
    await handle.cancel()
    return await _print_outcome(handle, ref)


def _handle(client: Client, ref: str) -> WorkflowHandle[Any, RunTaskResult]:
    """A handle typed with RunTask.run, so result() returns a RunTaskResult."""
    return client.get_workflow_handle_for(RunTask.run, workflow_id_for(ref))


async def _print_outcome(handle: WorkflowHandle[Any, RunTaskResult], ref: str) -> int:
    """Waits for the workflow to end, then prints its result or why it did not finish."""
    try:
        result: RunTaskResult = await handle.result()
    except WorkflowFailureError as err:
        if isinstance(err.cause, CancelledError):
            print(f"[status] {ref}: canceled")
            return 0
        print(f"[status] {ref}: failed: {err.cause}")
        return 1
    for attempt in result.attempts:
        print(f"[status] run {attempt.attempt}: {attempt.candidate}  {attempt.decision}  {attempt.detail}")
    h = result.harness
    if h is None:
        # A task an operator sent back picked up at its open pull request and made no run of its own to publish.
        print(f"[status] {ref}: resumed at its pull request: {result.outcome} ({result.outcome_reason})")
        return 0
    print(
        f"[status] {ref}: {h.status} ({h.end_reason})  turns={h.turns}  failed calls={h.failed_calls}  "
        f"tokens in={h.tokens_in} out={h.tokens_out}  usd={h.usd:.6f}"
    )
    print(f"[status] run grant: the HTTP Gateway counted ${h.grant_spent_usd or '?'} of the ${h.spend_limit_usd} limit")
    print(f"[status] files changed: {', '.join(h.files_changed) or 'none'}")
    print(f"[status] answer: {' '.join((h.answer or '').split()) or None}")
    return 0


async def _current_run_id(handle: WorkflowHandle[Any, RunTaskResult]) -> str | None:
    try:
        return await handle.query(RunTask.current_run_id)
    except (RPCError, WorkflowQueryFailedError):
        return None


async def _harness_get(path: str, run_id: str | None) -> dict[str, Any] | None:
    if not run_id:
        return None
    try:
        box = await sandbox.find(run_id)
    except sandbox.SandboxError:
        return None
    if box is None:
        return None
    try:
        async with harness_client.client(box) as http:
            response = await http.get(path, params={"run_id": run_id})
    except httpx.HTTPError:
        return None
    return response.json() if response.status_code == 200 else None


async def main(args: argparse.Namespace) -> int:
    client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)
    try:
        if args.command == "list":
            return await list_tasks(client)
        if not args.ref:
            print(f"[{args.command}] name the task: REF=<ref>")
            return 2
        command = {"status": status, "watch": watch, "cancel": cancel}[args.command]
        return await command(client, args.ref)
    except RPCError as err:
        print(f"[{args.command}] {err}")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["list", "status", "watch", "cancel"])
    parser.add_argument("ref", nargs="?", default="")
    try:
        sys.exit(asyncio.run(main(parser.parse_args())))
    except KeyboardInterrupt:
        sys.exit(130)
