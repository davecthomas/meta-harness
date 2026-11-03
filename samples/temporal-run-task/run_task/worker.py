"""Runs the RunTask workflow and its activities on the run-task task queue.

Stops on SIGINT or SIGTERM. Shutdown cancels a running run_harness, which
stops its pi subprocess before the worker exits.

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

from run_task.activities import prepare, run_harness
from run_task.shared import TASK_QUEUE, TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE
from run_task.workflows import RunTask

LOG_TAGS = {"temporalio.workflow": "[workflow]", "temporalio.activity": "[activity]"}


class _TagFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        tag = LOG_TAGS.get(record.name, f"[{record.name}]")
        return f"{tag} {super().format(record)}"


def _configure_logging() -> None:
    """Sends the SDK's workflow and activity loggers to stdout, which make run writes to worker.log.

    The SDK appends the full workflow or activity info to every message by
    default. The sample's messages name the ids they need, so that is turned off.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_TagFormatter("%(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # httpx logs every request at INFO. The activity prints what each call did.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    workflow.logger.workflow_info_on_message = False
    activity.logger.activity_info_on_message = False


async def main() -> None:
    _configure_logging()
    client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[RunTask],
        activities=[prepare, run_harness],
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
    print("[worker] stopped", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
