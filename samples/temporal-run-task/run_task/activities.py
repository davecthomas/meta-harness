"""The two activities: prepare builds the prompt, run_harness runs it on the harness server.

run_harness stands in for the design's harness engine call. It starts the run
with POST /run, follows GET /events until run_finished, and reads GET /usage.
The harness server (images/harness-pi) runs pi in its own process, so the run
keeps going when this worker dies.

The run id is the workflow id plus the activity name, the same on every
attempt. A retried attempt sends the same POST /run, gets the run already in
progress, and reads its events from the last event id it heartbeated. It never
starts pi a second time.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from run_task.shared import HarnessRequest, HarnessResult, PreparedRun, RunTaskInput

SAMPLE_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = SAMPLE_DIR / "prompts"
HARNESS_URL = os.environ.get("HARNESS_URL", "http://127.0.0.1:8090")

# The profile the harness server runs pi with. In the full design it comes from harness_profiles.json.
PROFILE = {"name": "pi-sample", "tools": ["read", "write", "edit", "ls"], "max_turns": 20, "max_minutes": 10}

# The harness server sends a heartbeat event every few seconds, so a stream that is quiet this long is dead.
EVENT_READ_TIMEOUT_SECONDS = 30.0


@activity.defn
async def prepare(request: RunTaskInput) -> PreparedRun:
    """Builds the prompt from a versioned template file."""
    path = PROMPTS_DIR / f"{request.template}.txt"
    if not path.is_file():
        raise ApplicationError(f"no prompt template {path.name}", type="UnknownTemplate", non_retryable=True)
    prompt = path.read_text(encoding="utf-8").strip()
    info = activity.info()
    activity.logger.info("prepare attempt %d: read the prompt from prompts/%s", info.attempt, path.name)
    return PreparedRun(template=request.template, prompt=prompt)


@activity.defn
async def run_harness(request: HarnessRequest) -> HarnessResult:
    """Starts or re-attaches to the run on the harness server and follows it to the end."""
    info = activity.info()
    tag = f"[run_harness attempt {info.attempt}]"
    run_id = f"{info.workflow_id}:run_harness"
    progress = _progress_from(info.heartbeat_details)
    activity.logger.info(
        "run_harness attempt %d started for %s, heartbeat timeout %ss",
        info.attempt,
        info.workflow_id,
        int(info.heartbeat_timeout.total_seconds()) if info.heartbeat_timeout else 0,
    )
    gateway_key = os.environ.get("GATEWAY_API_KEY")
    server_token = os.environ.get("HARNESS_SERVER_TOKEN")
    if not gateway_key or not server_token:
        raise ApplicationError(
            "set GATEWAY_API_KEY and HARNESS_SERVER_TOKEN in the worker's environment",
            type="HarnessConfigError",
            non_retryable=True,
        )

    async with httpx.AsyncClient(
        base_url=HARNESS_URL, headers={"authorization": f"Bearer {server_token}"}, timeout=10.0
    ) as client:
        reattached = await _start_or_reattach(client, tag, run_id, request, info.workflow_id, gateway_key, progress)
        activity.heartbeat(progress)
        finished: dict[str, Any] | None = None
        heartbeats = 1
        try:
            async for event in _events_after(client, progress["last_event_id"]):
                progress["last_event_id"] = event["id"]
                if event["run_id"] == run_id:
                    _record(progress, event)
                    line = _describe(event)
                    if line:
                        print(f"{tag} #{event['id']} {line}", flush=True)
                activity.heartbeat(progress)
                heartbeats += 1
                if event["run_id"] == run_id and event["type"] == "run_finished":
                    finished = event
                    break
        except asyncio.CancelledError:
            await _on_cancel(client, tag)
            raise
        except httpx.HTTPError as err:
            raise ApplicationError(f"lost the harness server's event stream: {err}", type="HarnessUnavailable") from err
        if finished is None:
            raise ApplicationError("the event stream ended before run_finished", type="HarnessUnavailable")
        usage = (await client.get("/usage", params={"run_id": run_id})).json()

    data = finished["data"]
    if data["status"] != "FINISHED":
        # The run itself ended. Retrying would re-attach to the same ended run, so the attempt fails for good.
        raise ApplicationError(
            f"run {run_id} ended {data['status']} ({data['end_reason']}): {data.get('error')}",
            type="HarnessRunFailed",
            non_retryable=True,
        )
    activity.logger.info(
        "run_harness attempt %d: run finished, %d heartbeats sent, returning the result to Temporal",
        info.attempt,
        heartbeats,
    )
    return HarnessResult(
        attempt=info.attempt,
        run_id=run_id,
        status=data["status"],
        end_reason=data["end_reason"],
        reattached=reattached,
        turns=data["turns"],
        failed_calls=usage["failed_calls"],
        tokens_in=usage["input_tokens"],
        tokens_out=usage["output_tokens"],
        usd=usage["usd"],
        files_changed=progress["files_changed"],
        answer=progress["answer"],
    )


async def _start_or_reattach(
    client: httpx.AsyncClient,
    tag: str,
    run_id: str,
    request: HarnessRequest,
    session_id: str,
    gateway_key: str,
    progress: dict[str, Any],
) -> bool:
    """POST /run. Returns True when the run already existed, so this attempt re-attached to it."""
    body = {
        "run_id": run_id,
        "harness_session_id": session_id,
        "prompt": request.prompt,
        "profile": PROFILE,
        # Until the gateway mints run tokens, the run's token is the gateway key, and the request names the model.
        "token": gateway_key,
        "engine": request.engine,
        "model": request.model,
    }
    try:
        response = await client.post("/run", json=body)
    except httpx.HTTPError as err:
        raise ApplicationError(f"no harness server at {HARNESS_URL}: {err}", type="HarnessUnavailable") from err
    if response.status_code == 202:
        print(f"{tag} POST /run started run {run_id}", flush=True)
        return False
    if response.status_code == 200:
        status = response.json()["status"]
        print(
            f"{tag} POST /run: run {run_id} already exists ({status}). "
            f"Re-attaching after event {progress['last_event_id']}, without starting pi again",
            flush=True,
        )
        return True
    error = response.json().get("error", response.text)
    if response.status_code in (400, 401, 409):
        raise ApplicationError(
            f"harness server refused the run ({response.status_code}): {error}",
            type="HarnessConfigError",
            non_retryable=True,
        )
    raise ApplicationError(f"harness server answered {response.status_code}: {error}", type="HarnessUnavailable")


async def _events_after(client: httpx.AsyncClient, last_event_id: int) -> AsyncIterator[dict[str, Any]]:
    """GET /events from Last-Event-ID, yielding each event as a dict."""
    timeout = httpx.Timeout(10.0, read=EVENT_READ_TIMEOUT_SECONDS)
    async with client.stream(
        "GET", "/events", headers={"Last-Event-ID": str(last_event_id)}, timeout=timeout
    ) as stream:
        stream.raise_for_status()
        data: list[str] = []
        async for line in stream.aiter_lines():
            if line.startswith("data: "):
                data.append(line[6:])
            elif line == "" and data:
                yield json.loads("\n".join(data))
                data = []


async def _on_cancel(client: httpx.AsyncClient, tag: str) -> None:
    """Stops pi when the workflow was canceled. Leaves the run going when only this worker is stopping."""
    details = activity.cancellation_details()
    if details and details.cancel_requested:
        print(f"{tag} cancellation received (cancel_requested), sending POST /cancel", flush=True)
        response = await client.post("/cancel")
        print(f"{tag} harness server stopped pi: run {response.json().get('status')}", flush=True)
        return
    reason = "worker shutdown" if activity.is_worker_shutdown() else "unknown"
    print(
        f"{tag} cancellation received ({reason}). The run keeps going on the harness server, "
        "and the next attempt re-attaches",
        flush=True,
    )


def _progress_from(heartbeat_details: object) -> dict[str, Any]:
    """What an earlier attempt saw, from its last heartbeat: the last event id, the files and the answer."""
    progress: dict[str, Any] = {"last_event_id": 0, "files_changed": [], "answer": None}
    if isinstance(heartbeat_details, (list, tuple)) and heartbeat_details and isinstance(heartbeat_details[-1], dict):
        progress.update(heartbeat_details[-1])
    return progress


def _record(progress: dict[str, Any], event: dict[str, Any]) -> None:
    data = event["data"]
    if event["type"] == "file_changed" and data["path"] not in progress["files_changed"]:
        progress["files_changed"].append(data["path"])
    if event["type"] == "turn_finished" and isinstance(data.get("text"), str) and data["text"]:
        progress["answer"] = " ".join(data["text"].split())


def _describe(event: dict[str, Any]) -> str | None:
    """One readable line per event. Heartbeats are not printed."""
    d = event["data"]
    match event["type"]:
        case "turn_started":
            return f"turn {d['turn']} starts"
        case "model_call":
            if d.get("error"):
                return f"model call failed: {d['error']}"
            calls = ",".join(d["tool_calls"]) or "none"
            return (
                f"model call: {d['engine']}:{d['model']}  finish={d['finish_reason']}  tool_calls={calls}  "
                f"tokens in={d['input_tokens']} out={d['output_tokens']} cached={d['cached_input_tokens']}  usd={d['usd']}"
            )
        case "tool_call":
            return f"runs tool {d['tool']} {json.dumps(d['args'], ensure_ascii=False)[:160]}"
        case "tool_result":
            return f"tool {d['tool']} failed: {str(d['output'])[:160]}" if d["is_error"] else f"tool {d['tool']} done"
        case "file_changed":
            return f"file changed: {d['path']}"
        case "turn_finished":
            text = " ".join(str(d.get("text") or "").split())
            return f"turn {d['turn']} finished: {text[:200]}" if text else f"turn {d['turn']} finished"
        case "compacted":
            return f"pi compacted the conversation ({d['reason']})"
        case "run_finished":
            return f"run finished: {d['status']} ({d['end_reason']})" + (f": {d['error']}" if d.get("error") else "")
    return None
