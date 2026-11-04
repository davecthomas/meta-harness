"""The two activities: prepare builds the prompt, run_harness runs it in a per-run sandbox.

run_harness is the design's run service run activity (section 6.3). Its Lease
step starts the run's per-run sandbox: a Docker container of the harness-pi
image, named after the run id (run_task/sandbox.py). Its Start step sends
POST /run to the harness server in it, the Wait step follows GET /events until
run_finished, and the Collect step reads GET /usage, revokes the run grant and
removes the container. pi runs in the container, so the run keeps going when
this worker dies, and the next attempt finds the same container.

The run id is the workflow id plus the activity name, the same on every
attempt. A retried attempt sends the same POST /run, gets the run already in
progress, and reads its events from the last event id it heartbeated. It never
starts pi a second time.

Before POST /run, the activity asks the HTTP Gateway for a run grant: the
run's engine and model, its spend limit, and an expiry. The harness server
gets only the grant key. The activity revokes the grant once the run has
ended, however it ended. A worker that stops mid-run leaves the grant live,
because the run keeps going and the next attempt re-attaches to it. That
attempt asks for the grant again and gets a new key for the same grant.
"""

import asyncio
from pathlib import Path
from typing import Any

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from run_task import harness_client, run_grants, sandbox
from run_task.shared import INLINE_TEMPLATE, HarnessRequest, HarnessResult, PreparedRun, RunTaskInput, ref_for

SAMPLE_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = SAMPLE_DIR / "prompts"

# The profile the harness server runs pi with. In the full design it comes from harness_profiles.json.
PROFILE = {"name": "pi-sample", "tools": ["read", "write", "edit", "ls"], "max_turns": 20, "max_minutes": 10}
# The grant outlives the run's own time limit by this much, so it never expires under a run still ending.
GRANT_GRACE_SECONDS = 300


@activity.defn
async def prepare(request: RunTaskInput) -> PreparedRun:
    """Uses the task text the caller gave, or builds the prompt from a versioned template file."""
    info = activity.info()
    if request.task:
        activity.logger.info("prepare attempt %d: using the task text from the caller", info.attempt)
        return PreparedRun(template=INLINE_TEMPLATE, prompt=request.task.strip())
    path = PROMPTS_DIR / f"{request.template}.txt"
    if not path.is_file():
        raise ApplicationError(f"no prompt template {path.name}", type="UnknownTemplate", non_retryable=True)
    prompt = path.read_text(encoding="utf-8").strip()
    activity.logger.info("prepare attempt %d: read the prompt from prompts/%s", info.attempt, path.name)
    return PreparedRun(template=request.template, prompt=prompt)


@activity.defn
async def run_harness(request: HarnessRequest) -> HarnessResult:
    """Starts or re-attaches to the run on the harness server and follows it to the end."""
    info = activity.info()
    tag = f"[{info.workflow_id} run_harness attempt {info.attempt}]"
    run_id = f"{info.workflow_id}:run_harness"
    progress = _progress_from(info.heartbeat_details)
    activity.logger.info(
        "run_harness attempt %d started for %s, heartbeat timeout %ss",
        info.attempt,
        info.workflow_id,
        int(info.heartbeat_timeout.total_seconds()) if info.heartbeat_timeout else 0,
    )
    if not run_grants.gateway_key():
        raise ApplicationError(
            "set GATEWAY_API_KEY in the worker's environment", type="HarnessConfigError", non_retryable=True
        )

    ref = ref_for(info.workflow_id)
    box = await _lease(tag, run_id, ref)
    try:
        grant_key = await _issue_grant(tag, run_id, request, info.workflow_id)
        async with harness_client.client(box) as client:
            reattached = await _start_or_reattach(
                client, tag, run_id, request, grant_key, info.workflow_id, progress, box.url
            )
            activity.heartbeat(progress)
            finished: dict[str, Any] | None = None
            heartbeats = 1
            try:
                async for event in harness_client.events_after(client, progress["last_event_id"]):
                    progress["last_event_id"] = event["id"]
                    if event["run_id"] == run_id:
                        _record(progress, event)
                        line = harness_client.describe(event)
                        if line:
                            print(f"{tag} #{event['id']} {line}", flush=True)
                    activity.heartbeat(progress)
                    heartbeats += 1
                    if event["run_id"] == run_id and event["type"] == "run_finished":
                        finished = event
                        break
            except asyncio.CancelledError:
                if await _on_cancel(client, tag):
                    await _revoke_grant(tag, run_id)
                    await _release(tag, run_id, ref)
                raise
            except httpx.HTTPError as err:
                raise ApplicationError(
                    f"lost the harness server's event stream: {err}", type="HarnessUnavailable"
                ) from err
            if finished is None:
                raise ApplicationError("the event stream ended before run_finished", type="HarnessUnavailable")
            usage = (await client.get("/usage", params={"run_id": run_id})).json()
    except ApplicationError as err:
        # No later attempt will re-attach, so nothing needs the per-run sandbox.
        if err.non_retryable:
            await _release(tag, run_id, ref)
        raise

    # The run has ended, so its grant key and its per-run sandbox have nothing left to do.
    grant_spent = await _revoke_grant(tag, run_id)
    await _release(tag, run_id, ref)
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
        spend_limit_usd=request.spend_limit_usd,
        grant_spent_usd=grant_spent,
    )


async def _lease(tag: str, run_id: str, ref: str) -> sandbox.Sandbox:
    """The Lease step: the run's per-run sandbox, started now or found from an earlier attempt."""
    try:
        box = await sandbox.ensure(run_id, ref)
    except sandbox.SandboxError as err:
        raise ApplicationError(str(err), type="SandboxUnavailable", non_retryable=True) from err
    if box.created:
        print(
            f"{tag} per-run sandbox {box.name} started from {sandbox.IMAGE} on its own network "
            f"{sandbox.network_name(run_id)}, out only through the egress proxy. "
            f"Workspace {sandbox.workspace_for(ref)} mounted at /workspace",
            flush=True,
        )
    else:
        print(f"{tag} per-run sandbox {box.name} is still running from an earlier attempt", flush=True)
    return box


async def _release(tag: str, run_id: str, ref: str) -> None:
    """Saves the harness server's log and removes the run's per-run sandbox."""
    await sandbox.destroy(run_id, ref)
    print(
        f"{tag} per-run sandbox {sandbox.container_name(run_id)} and its network removed, "
        f"harness server log: {sandbox.log_path_for(ref)}",
        flush=True,
    )


async def _issue_grant(tag: str, run_id: str, request: HarnessRequest, workflow_id: str) -> str:
    """Asks the HTTP Gateway for the run's grant and returns its key."""
    try:
        grant = await run_grants.issue(
            run_id=run_id,
            engine=request.engine,
            model=request.model,
            spend_limit_usd=request.spend_limit_usd,
            ttl_seconds=int(PROFILE["max_minutes"]) * 60 + GRANT_GRACE_SECONDS,
            workflow_id=workflow_id,
            tags={"profile": str(PROFILE["name"])},
        )
    except run_grants.RunGrantError as err:
        # A refusal is the request's fault and retrying repeats it. No answer, or a 503, may pass.
        if err.status in (400, 401, 409, 422):
            raise ApplicationError(str(err), type="HarnessConfigError", non_retryable=True) from err
        raise ApplicationError(str(err), type="GatewayUnavailable") from err
    if grant["created"]:
        print(
            f"{tag} run grant issued for {run_id}: {grant['engine']}:{grant['model']}, "
            f"spend limit ${grant['spend_limit_usd']}, expires {grant['expires_at']}",
            flush=True,
        )
    else:
        print(
            f"{tag} run grant for {run_id} is still live (spent ${grant['spent_usd']} of ${grant['spend_limit_usd']}). "
            "The HTTP Gateway added a key to it",
            flush=True,
        )
    return str(grant["grant_key"])


async def _revoke_grant(tag: str, run_id: str) -> str | None:
    """Revokes the run's grant and returns what the HTTP Gateway counted against it.

    A failure here does not fail the run: the grant expires on its own.
    """
    try:
        revoked = await run_grants.revoke(run_id)
    except run_grants.RunGrantError as err:
        print(f"{tag} could not revoke the run grant, so it stays live until it expires: {err}", flush=True)
        return None
    print(f"{tag} run grant revoked: the HTTP Gateway counted ${revoked['spent_usd']} against it", flush=True)
    return str(revoked["spent_usd"])


async def _start_or_reattach(
    client: httpx.AsyncClient,
    tag: str,
    run_id: str,
    request: HarnessRequest,
    grant_key: str,
    session_id: str,
    progress: dict[str, Any],
    harness_url: str,
) -> bool:
    """POST /run. Returns True when the run already existed, so this attempt re-attached to it."""
    body = {
        "run_id": run_id,
        "harness_session_id": session_id,
        "prompt": request.prompt,
        "profile": PROFILE,
        # The grant decides the engine, the model and the spend limit. The harness server sees only the key.
        "grant_key": grant_key,
    }
    try:
        response = await client.post("/run", json=body)
    except httpx.HTTPError as err:
        raise ApplicationError(f"no harness server at {harness_url}: {err}", type="HarnessUnavailable") from err
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


async def _on_cancel(client: httpx.AsyncClient, tag: str) -> bool:
    """Stops pi when the workflow was canceled. Leaves the run going when only this worker is stopping.

    Returns True when the run was stopped, so its grant can be revoked.
    """
    details = activity.cancellation_details()
    if details and details.cancel_requested:
        print(f"{tag} cancellation received (cancel_requested), sending POST /cancel", flush=True)
        response = await client.post("/cancel")
        print(f"{tag} harness server stopped pi: run {response.json().get('status')}", flush=True)
        return True
    reason = "worker shutdown" if activity.is_worker_shutdown() else "unknown"
    print(
        f"{tag} cancellation received ({reason}). The run keeps going on the harness server, "
        "its grant stays live, and the next attempt re-attaches",
        flush=True,
    )
    return False


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
