"""The RunTask workflow: prepare, then run the harness.

Workflow code runs in the SDK's sandbox and must be deterministic: no I/O, no
clocks, no subprocesses. All of that lives in the activities. The activity and
payload modules are passed through the sandbox so it does not reload them.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from run_task.activities import prepare, run_harness
    from run_task.shared import HarnessRequest, RunTaskInput, RunTaskResult


@workflow.defn
class RunTask:
    @workflow.run
    async def run(self, request: RunTaskInput) -> RunTaskResult:
        prepared = await workflow.execute_activity(
            prepare,
            request,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        harness = await workflow.execute_activity(
            run_harness,
            HarnessRequest(prompt=prepared.prompt, engine=request.engine, model=request.model),
            # The whole pi run, from start to the [run] line.
            start_to_close_timeout=timedelta(minutes=10),
            # A worker that stops heartbeating for this long is treated as gone, and the attempt is retried.
            heartbeat_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=2),
                maximum_attempts=3,
                non_retryable_error_types=["HarnessConfigError"],
            ),
            # On cancel, wait until the activity has stopped pi before the workflow ends.
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )
        return RunTaskResult(template=prepared.template, prompt=prepared.prompt, harness=harness)
