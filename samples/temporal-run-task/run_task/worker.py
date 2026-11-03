"""Runs the RunTask workflow and its activities on the run-task task queue.

Stops on SIGINT or SIGTERM. Shutdown cancels a running run_harness, which
stops its pi subprocess before the worker exits.
"""

import asyncio
import signal
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from run_task.activities import prepare, run_harness
from run_task.shared import TASK_QUEUE, TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE
from run_task.workflows import RunTask


async def main() -> None:
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
