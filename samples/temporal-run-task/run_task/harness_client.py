"""Reading the harness server's event stream, shared by the activity and the command-line tools."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from temporalio.client import WorkflowQueryFailedError
from temporalio.service import RPCError

from run_task import sandbox

# The harness server sends a heartbeat event every few seconds, so a stream that is quiet this long is dead.
EVENT_READ_TIMEOUT_SECONDS = 30.0


def client(box: sandbox.Sandbox) -> httpx.AsyncClient:
    """An HTTP client for the harness server in a per-run sandbox, through the egress proxy's control listener."""
    return httpx.AsyncClient(
        base_url=box.url, headers={"authorization": f"Bearer {box.token}"}, proxy=box.proxy, timeout=10.0
    )


async def events_after(client: httpx.AsyncClient, last_event_id: int) -> AsyncIterator[dict[str, Any]]:
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


async def follow(box: sandbox.Sandbox, run_id: str, prefix: str) -> dict[str, Any] | None:
    """Prints one line per event of the run until run_finished, and returns that event.

    Returns None when the harness server is gone, which happens once the task
    that started it has ended.
    """
    async with client(box) as http:
        try:
            async for event in events_after(http, 0):
                if event["run_id"] != run_id:
                    continue
                line = describe(event)
                if line:
                    print(f"{prefix} #{event['id']} {line}", flush=True)
                if event["type"] == "run_finished":
                    return event
        except httpx.HTTPError:
            return None
    return None


async def follow_run(run_id: str, prefix: str, wait_seconds: float) -> dict[str, Any] | None:
    """follow() on the run's per-run sandbox, waiting up to wait_seconds for its container to start.

    Returns None when no per-run sandbox turns up, or once it is gone.
    """
    deadline = asyncio.get_running_loop().time() + wait_seconds
    while True:
        try:
            box = await sandbox.find(run_id)
        except sandbox.SandboxError:
            return None
        if box:
            return await follow(box, run_id, prefix)
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(0.5)


async def follow_task(handle: Any, prefix: str) -> None:
    """Prints the events of each run a task makes, in turn, until the task ends.

    Asks the RunTask workflow which run it is on (its current_run_id query),
    follows that run's per-run sandbox, then asks again for the next one.
    """
    followed: set[str] = set()
    while True:
        try:
            run_id = await handle.query("current_run_id")
        except (RPCError, WorkflowQueryFailedError):
            run_id = None
        if run_id and run_id not in followed:
            followed.add(run_id)
            print(f"{prefix} following run {run_id}", flush=True)
            await follow_run(run_id, prefix, wait_seconds=60)
            continue
        try:
            described = await handle.describe()
        except RPCError:
            return
        if described.status is not None and described.status.name != "RUNNING":
            return
        await asyncio.sleep(1)


def describe(event: dict[str, Any]) -> str | None:
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
