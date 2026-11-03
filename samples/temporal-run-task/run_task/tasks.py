"""Looks at tasks the starter started: list them, show one, watch one, cancel one.

    python -m run_task.tasks list
    python -m run_task.tasks status REF
    python -m run_task.tasks watch REF
    python -m run_task.tasks cancel REF

REF is the name given to `make task` or `make run`. The workflow memo holds
the task's harness server URL and workspace, so these commands need nothing
else. The harness server stops when its task ends, so `watch` and the live
part of `status` work only while the task runs.

Environment: HARNESS_SERVER_TOKEN, TEMPORAL_ADDRESS (default localhost:7233).
"""

import argparse
import asyncio
import sys
from typing import Any

import httpx
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError, WorkflowHandle
from temporalio.exceptions import CancelledError
from temporalio.service import RPCError

from run_task import harness_client
from run_task.shared import TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE, RunTaskResult, workflow_id_for
from run_task.workflows import RunTask


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
        run = await _harness_get(memo, "/status", ref)
        usage = await _harness_get(memo, "/usage", ref)
        if run and usage:
            print(
                f"[status] run {run['run_id']}: {run['status']}  turns={run['turns']}  "
                f"calls={usage['calls']} failed={usage['failed_calls']}  "
                f"tokens in={usage['input_tokens']} out={usage['output_tokens']}  usd={usage['usd']:.6f}"
            )
        else:
            print("[status] the harness server has not started the run yet")
        return 0
    return await _print_outcome(handle, ref)


async def watch(client: Client, ref: str) -> int:
    handle = _handle(client, ref)
    memo = await (await handle.describe()).memo()
    token = harness_client.server_token() or ""
    finished = await harness_client.follow(memo["harness_url"], token, f"{handle.id}:run_harness", f"[{ref}]")
    if finished is None:
        print(f"[watch] the harness server for {ref} is gone, so the task has ended")
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
    h = result.harness
    print(
        f"[status] {ref}: {h.status} ({h.end_reason})  turns={h.turns}  failed calls={h.failed_calls}  "
        f"tokens in={h.tokens_in} out={h.tokens_out}  usd={h.usd:.6f}"
    )
    print(f"[status] files changed: {', '.join(h.files_changed) or 'none'}")
    print(f"[status] answer: {h.answer}")
    return 0


async def _harness_get(memo: dict[str, Any], path: str, ref: str) -> dict[str, Any] | None:
    token = harness_client.server_token() or ""
    try:
        async with harness_client.client(memo["harness_url"], token) as http:
            response = await http.get(path, params={"run_id": f"{workflow_id_for(ref)}:run_harness"})
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
