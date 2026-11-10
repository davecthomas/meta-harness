"""The meta-harness scheduler service: an admission pass every MH_ADMISSION_SECONDS, and its REST API part.

    GET  /v1/kill-switch     whether the kill switch is on, who changed it and why
    POST /v1/kill-switch     turn it on or off: {"engaged": true, "reason": "..."}, or set a mode:
                             {"mode": "drain"} admits nothing new and lets running tasks finish,
                             "stop" is engaged, "off" ends both
    GET  /health

Operators call it with an operator key (MH_OPERATOR_KEYS); control-plane
services with a service key, naming the operator in X-MH-Actor.

How many tasks run at once is the task policy's max_parallel (config/task_policy.json,
or its active configuration version), no more than MH_MAX_SANDBOXES.

Environment: MH_MAX_SANDBOXES, MH_ADMISSION_SECONDS (default 3),
MH_TASKS_URL with MH_SERVICE_KEY, MH_PLANNER_URL, MH_EVENTS_URL, and the
Temporal address the run service uses.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from typing import Literal

from pydantic import BaseModel

from meta_harness import auth
from meta_harness.scheduler import admission as admission_module
from meta_harness.scheduler.admission import Admission

log = logging.getLogger("meta_harness.scheduler")


class KillSwitchIn(BaseModel):
    engaged: bool | None = None
    reason: str | None = None
    # stop: the kill switch on. drain: no new task starts, running ones finish. off: neither.
    mode: Literal["stop", "drain", "off"] | None = None


def create_app(
    admission: Admission,
    interval: float | None = None,
    operator_keys: dict[str, str] | None = None,
    service_keys: dict[str, str] | None = None,
) -> FastAPI:
    every = interval if interval is not None else float(os.environ.get("MH_ADMISSION_SECONDS", "3"))
    operators = operator_keys if operator_keys is not None else auth.keys_from_env("MH_OPERATOR_KEYS")
    services = service_keys if service_keys is not None else auth.keys_from_env("MH_SERVICE_KEYS")
    state = {"passes": 0, "last_error": None}

    async def admit_forever() -> None:
        while True:
            try:
                result = await admission.pass_once()
                state["passes"] += 1
                state["last_error"] = None
                if result.started:
                    log.info("admitted %s", ", ".join(result.started))
                if result.canceled:
                    log.info("canceled %s", ", ".join(result.canceled))
            except Exception as err:  # noqa: BLE001 - one failed pass must not stop admission
                state["last_error"] = str(err)
                log.warning("admission pass failed: %s", err)
            await asyncio.sleep(every)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.create_task(admit_forever()) if every > 0 else None
        try:
            yield
        finally:
            if loop:
                loop.cancel()

    app = FastAPI(title="meta-harness scheduler", lifespan=lifespan)

    def operator(key: str = Depends(auth.bearer), actor: str | None = Header(default=None, alias="X-MH-Actor")) -> str:
        if label := auth.label_for(key, operators):
            return label
        if label := auth.label_for(key, services):
            return actor or label
        raise HTTPException(status_code=401, detail="unknown API key")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", **state}

    @app.get("/v1/kill-switch")
    def read_kill_switch(who: str = Depends(operator)) -> dict:
        return admission_module.kill_switch()

    @app.post("/v1/kill-switch")
    def change_kill_switch(body: KillSwitchIn, who: str = Depends(operator)) -> dict:
        if body.mode is None and body.engaged is None:
            raise HTTPException(status_code=422, detail="give engaged, or a mode: stop, drain or off")
        if body.mode == "drain":
            switch = admission_module.set_draining(True, who, body.reason)
        else:
            engaged = body.mode == "stop" if body.mode else bool(body.engaged)
            if body.mode == "off":
                admission_module.set_draining(False, who, body.reason)
            switch = admission_module.set_kill_switch(engaged, who, body.reason)
        admission.events.emit(
            "kill_switch_changed",
            {"engaged": switch["engaged"], "reason": body.reason, "mode": mode_of(switch)},
            actor=who,
        )
        return switch

    return app


def mode_of(switch: dict) -> str:
    """The kill switch's mode: stop when it is on, drain while draining, otherwise off."""
    return "stop" if switch["engaged"] else "drain" if switch.get("draining") else "off"


async def start_run_task(task_id: str) -> None:
    """Starts the task's RunTask workflow. One that already exists counts as started."""
    from meta_harness.run_service import start

    try:
        await start.start(task_id)
    except start.AlreadyStarted:
        pass


async def cancel_run_task(task_id: str) -> None:
    """Asks the task's RunTask workflow to stop. Its run activity stops pi, and the workflow marks the task canceled."""
    from meta_harness.run_service import start

    await (await start.client()).get_workflow_handle(task_id).cancel()


async def signal_run_task(task_id: str, message: dict) -> None:
    """Hands an operator message to the task's RunTask workflow."""
    from meta_harness.run_service import start
    from meta_harness.run_service.shared import OperatorMessage

    handle = (await start.client()).get_workflow_handle(task_id)
    await handle.signal("operator_message", OperatorMessage(**message))
