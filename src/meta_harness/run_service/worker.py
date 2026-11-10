"""Runs the RunTask workflow and its activities on the run-task task queue.

Checks every tier candidate in config/models.json against
config/harness_profiles.json before it polls, and stops on the first bad one.

Stops on SIGINT or SIGTERM. Shutdown cancels a running run_harness attempt,
which leaves pi running in its per-run sandbox for the next attempt to find.

Log lines from workflow code start with [workflow], from activity code with
[activity]. The SDK's own warnings, such as an activity attempt that failed,
start with their logger name.
"""

import asyncio
import logging
import signal
import sys
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker

from meta_harness.run_service import events, routing
from meta_harness import logs
from meta_harness.logs import JournalFormatter
from meta_harness.run_service.activities import (
    deliver_message,
    cleanup_run,
    goal_auto_merge,
    loop_prompt,
    merge_pr,
    post_review,
    prepare,
    publish,
    review_policy,
    route,
    run_harness,
    wait_checks,
    wait_merge_checks,
)
from meta_harness.run_service.budget_activities import budget_hold, goal_budget
from meta_harness.run_service.prompt_activities import record_prompt
from meta_harness.run_service.resume_activities import resume_state
from meta_harness.run_service.shared import TASK_QUEUE, TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE
from meta_harness.run_service.task_activities import (
    load_task,
    record_attempt,
    record_published,
    record_route,
    set_task_status,
)
from meta_harness.run_service.workflows import RunTask

LOG_TAGS = {"temporalio.workflow": "[workflow]", "temporalio.activity": "[activity]"}


class _TagFormatter(JournalFormatter):
    def format(self, record: logging.LogRecord) -> str:
        tag = LOG_TAGS.get(record.name, f"[{record.name}]")
        return f"{tag} {super().format(record)}"


def _configure_logging() -> None:
    """Sends the SDK's workflow and activity loggers to stdout, which make run writes to worker.log.

    The SDK appends the full workflow or activity info to every message by
    default. The sample's messages name the ids they need, so that is turned off.
    """
    logs.configure(formatter=_TagFormatter("%(message)s"))
    workflow.logger.workflow_info_on_message = False
    activity.logger.activity_info_on_message = False


async def main() -> None:
    _configure_logging()
    try:
        for line in routing.validate():
            print(f"[worker] {line}", flush=True)
    except ValueError as err:
        print(f"[worker] the routing configuration is not usable: {err}", flush=True)
        sys.exit(1)
    client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[RunTask],
        activities=[
            prepare,
            route,
            run_harness,
            cleanup_run,
            publish,
            wait_checks,
            wait_merge_checks,
            review_policy,
            loop_prompt,
            post_review,
            goal_auto_merge,
            merge_pr,
            load_task,
            set_task_status,
            record_route,
            record_attempt,
            record_published,
            record_prompt,
            goal_budget,
            budget_hold,
            resume_state,
            deliver_message,
        ],
        # A cancel reaches an activity only in the reply to a heartbeat. The SDK holds
        # heartbeats back to 0.8 x the heartbeat timeout by default (8 s here); this
        # sends them every second, so a cancel stops pi within about a second.
        max_heartbeat_throttle_interval=timedelta(seconds=1),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    async with worker:
        print(f"[worker] polling task queue {TASK_QUEUE} on {TEMPORAL_ADDRESS}", flush=True)
        await stop.wait()
        print("[worker] shutting down", flush=True)
    # Send the events still in the outbox before the process ends.
    events.close()
    print("[worker] stopped", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
