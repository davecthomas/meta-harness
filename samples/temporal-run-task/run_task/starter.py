"""Starts one RunTask workflow, waits for it, and prints the result.

    python -m run_task.starter [--workflow-id ID]

Exit codes: 0 completed, 1 failed, 3 canceled, 130 interrupted.
Ctrl-C cancels the workflow before the starter exits.

Environment: SAMPLE_ENGINE, SAMPLE_MODEL (defaults claude, claude-haiku-4-5),
TEMPORAL_ADDRESS (default localhost:7233), TEMPORAL_UI_URL (default http://localhost:8233).
"""

import argparse
import asyncio
import os
import sys
import uuid
from datetime import timedelta

from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import CancelledError

from run_task.shared import (
    TASK_QUEUE,
    TEMPORAL_ADDRESS,
    TEMPORAL_NAMESPACE,
    RunTaskInput,
    RunTaskResult,
)
from run_task.workflows import RunTask

UI_URL = os.environ.get("TEMPORAL_UI_URL", "http://localhost:8233")
EXIT_CANCELED = 3


async def main(workflow_id: str) -> int:
    client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)
    request = RunTaskInput(
        template="hello-v1",
        engine=os.environ.get("SAMPLE_ENGINE") or "claude",
        model=os.environ.get("SAMPLE_MODEL") or "claude-haiku-4-5",
    )
    handle = await client.start_workflow(
        RunTask.run,
        request,
        id=workflow_id,
        task_queue=TASK_QUEUE,
        execution_timeout=timedelta(minutes=30),
    )
    print(f"[starter] started workflow {workflow_id}  engine={request.engine}  model={request.model}", flush=True)
    print(f"[starter] web UI: {UI_URL}/namespaces/{TEMPORAL_NAMESPACE}/workflows/{workflow_id}", flush=True)

    try:
        result: RunTaskResult = await handle.result()
    except WorkflowFailureError as err:
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

    h = result.harness
    print(f"[starter] workflow {workflow_id} completed", flush=True)
    print(f"[result] template={result.template}  attempt={h.attempt}  exit_code={h.exit_code}", flush=True)
    print(
        f"[result] turns={h.turns}  failed={h.failed_turns}  tokens in={h.tokens_in} out={h.tokens_out}  usd={h.usd:.6f}",
        flush=True,
    )
    print(f"[result] hello.txt: {h.file_content}", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--workflow-id", default=f"run-task-{uuid.uuid4().hex[:8]}", help="workflow id (default: run-task-<random>)"
    )
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(main(args.workflow_id)))
    except KeyboardInterrupt:
        sys.exit(130)
