"""The meta-harness scheduler service: an admission pass every MH_ADMISSION_SECONDS, and /health.

Environment: MH_MAX_RUNNING (default 2), MH_ADMISSION_SECONDS (default 3),
MH_TASKS_URL with MH_SERVICE_KEY, MH_PLANNER_URL, MH_EVENTS_URL, and the
Temporal address the run service uses.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from meta_harness.scheduler.admission import Admission

log = logging.getLogger("meta_harness.scheduler")


def create_app(admission: Admission, interval: float | None = None) -> FastAPI:
    every = interval if interval is not None else float(os.environ.get("MH_ADMISSION_SECONDS", "3"))
    state = {"passes": 0, "last_error": None}

    async def admit_forever() -> None:
        while True:
            try:
                result = await admission.pass_once()
                state["passes"] += 1
                state["last_error"] = None
                if result.started:
                    log.info("admitted %s", ", ".join(result.started))
            except Exception as err:  # noqa: BLE001 - one failed pass must not stop admission
                state["last_error"] = str(err)
                log.warning("admission pass failed: %s", err)
            await asyncio.sleep(every)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.create_task(admit_forever())
        try:
            yield
        finally:
            loop.cancel()

    app = FastAPI(title="meta-harness scheduler", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", **state}

    return app


async def start_run_task(task_id: str) -> None:
    """Starts the task's RunTask workflow. One that already exists counts as started."""
    from meta_harness.run_service import start

    try:
        await start.start(task_id)
    except start.AlreadyStarted:
        pass
