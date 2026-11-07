"""Starts the RunTask workflow for a task from the task service.

The workflow id is the task id, and Temporal refuses a second workflow for it,
ever: a task runs in one RunTask workflow. Admission (the meta-harness
scheduler) will call this; until then `mh run start` does.
"""

from datetime import timedelta

from temporalio.client import Client, WorkflowFailureError
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from meta_harness.run_service import sandbox
from meta_harness.run_service.shared import (
    TASK_QUEUE,
    TEMPORAL_ADDRESS,
    TEMPORAL_NAMESPACE,
    RunTaskInput,
    RunTaskResult,
)
from meta_harness.run_service.workflows import RunTask

# A task's runs, fallbacks and escalations together.
EXECUTION_TIMEOUT = timedelta(hours=6)


class AlreadyStarted(RuntimeError):
    pass


async def client() -> Client:
    return await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)


async def start(task_id: str) -> str:
    """Starts the task's RunTask workflow and returns its workflow id."""
    temporal = await client()
    try:
        handle = await temporal.start_workflow(
            RunTask.run,
            RunTaskInput(task_id=task_id),
            id=task_id,
            task_queue=TASK_QUEUE,
            execution_timeout=EXECUTION_TIMEOUT,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
            memo={"ref": task_id, "workspace": str(sandbox.task_dir_for(task_id))},
        )
    except WorkflowAlreadyStartedError as err:
        raise AlreadyStarted(f"task {task_id} already has a RunTask workflow") from err
    return handle.id


async def wait(task_id: str) -> tuple[RunTaskResult | None, str]:
    """Waits for the task's RunTask workflow to end. Returns its result, or None and why it did not complete."""
    handle = (await client()).get_workflow_handle_for(RunTask.run, task_id)
    try:
        return await handle.result(), "completed"
    except WorkflowFailureError as err:
        return None, str(err.cause or err)
