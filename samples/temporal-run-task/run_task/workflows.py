"""The RunTask workflow: prepare, then run the harness.

Workflow code runs in the SDK's sandbox and must be deterministic: no I/O, no
clocks, no subprocesses. All of that lives in the activities. The activity and
payload modules are passed through the sandbox so it does not reload them.

workflow.logger skips its output while the worker replays history, so each
step logs once, when it first runs. A worker that picks the workflow up from
history prints one line saying it replayed.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, CancelledError

with workflow.unsafe.imports_passed_through():
    from run_task.activities import prepare, run_harness
    from run_task.shared import HarnessRequest, RunTaskInput, RunTaskResult

PREPARE_TIMEOUT = timedelta(seconds=30)
PREPARE_RETRY = RetryPolicy(maximum_attempts=3)
# The whole pi run, from start to the [run] line.
HARNESS_TIMEOUT = timedelta(minutes=10)
# A worker that stops heartbeating for this long is treated as gone, and the attempt is retried.
HARNESS_HEARTBEAT_TIMEOUT = timedelta(seconds=10)
HARNESS_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_attempts=3,
    non_retryable_error_types=["HarnessConfigError"],
)


@workflow.defn
class RunTask:
    @workflow.run
    async def run(self, request: RunTaskInput) -> RunTaskResult:
        info = workflow.info()
        log = workflow.logger
        if workflow.unsafe.is_replaying():
            # workflow.logger is silent during replay, so this one line uses print.
            print(
                f"[workflow] {info.workflow_id}: this worker has no state for the workflow, "
                "so it replays the history to rebuild it. Steps already in the history do not run again.",
                flush=True,
            )
        log.info(
            "%s: started, run id %s, template %s, engine %s, model %s",
            info.workflow_id,
            info.run_id,
            request.template,
            request.engine,
            request.model,
        )

        log.info(
            "%s: scheduling activity prepare (timeout %ss, up to %d attempts)",
            info.workflow_id,
            int(PREPARE_TIMEOUT.total_seconds()),
            PREPARE_RETRY.maximum_attempts,
        )
        prepared = await workflow.execute_activity(
            prepare, request, start_to_close_timeout=PREPARE_TIMEOUT, retry_policy=PREPARE_RETRY
        )
        log.info("%s: prepare returned a %d-character prompt", info.workflow_id, len(prepared.prompt))

        log.info(
            "%s: scheduling activity run_harness (timeout %dm, heartbeat timeout %ss, up to %d attempts)",
            info.workflow_id,
            int(HARNESS_TIMEOUT.total_seconds() // 60),
            int(HARNESS_HEARTBEAT_TIMEOUT.total_seconds()),
            HARNESS_RETRY.maximum_attempts,
        )
        try:
            harness = await workflow.execute_activity(
                run_harness,
                HarnessRequest(prompt=prepared.prompt, engine=request.engine, model=request.model),
                start_to_close_timeout=HARNESS_TIMEOUT,
                heartbeat_timeout=HARNESS_HEARTBEAT_TIMEOUT,
                retry_policy=HARNESS_RETRY,
                # On cancel, wait until the activity has stopped pi before the workflow ends.
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
        except ActivityError as err:
            if isinstance(err.cause, CancelledError):
                log.info("%s: canceled, and run_harness has stopped pi", info.workflow_id)
            else:
                log.info("%s: run_harness failed on its last attempt: %s", info.workflow_id, err.cause)
            raise
        log.info(
            "%s: run_harness returned on attempt %d: %d turns, usd %.6f",
            info.workflow_id,
            harness.attempt,
            harness.turns,
            harness.usd,
        )
        log.info("%s: completed", info.workflow_id)
        return RunTaskResult(template=prepared.template, prompt=prepared.prompt, harness=harness)
