"""Starts the RunTask workflow for a task from the task service.

The workflow id is the task id, and Temporal refuses a second workflow for it:
a task runs in one RunTask workflow, unless an operator sent it back to the
queue (retry, or a merge they asked for), which starts another once the
earlier one has ended. Admission (the meta-harness
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

# The longest one RunTask workflow lives: longer than any wait inside it. A workflow holds for budget for
# up to a day and waits on an operator's review with no limit of its own, so a six-hour limit, as this was,
# ended those workflows mid-wait with the task still running and no one to answer. Temporal ends a workflow
# at its limit without running any of its code, so nothing a workflow does can be allowed to outlast it. A
# workflow that dies some other way is the supervisor's to notice, not this limit's.
EXECUTION_TIMEOUT = timedelta(hours=72)


class AlreadyStarted(RuntimeError):
    pass


async def client() -> Client:
    return await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)


async def start(task_id: str, again: bool = False) -> str:
    """Starts the task's RunTask workflow and returns its workflow id.

    again: the task was sent back to the queue by an operator, so its earlier workflow has ended and a new one
    with the same id may follow it. Never two at once: one that is still running is refused either way.
    """
    temporal = await client()
    reuse = WorkflowIDReusePolicy.ALLOW_DUPLICATE if again else WorkflowIDReusePolicy.REJECT_DUPLICATE
    try:
        handle = await temporal.start_workflow(
            RunTask.run,
            RunTaskInput(task_id=task_id),
            id=task_id,
            task_queue=TASK_QUEUE,
            execution_timeout=EXECUTION_TIMEOUT,
            id_reuse_policy=reuse,
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
