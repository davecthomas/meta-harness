"""The activities: prepare builds the prompt, route picks the tier, run_harness runs one tier candidate.

route is the design's run service route activity (section 6.3). It sends the
task prompt to the ai-api-unified router in classify-only mode
(AiRouteClassifier, ai-api-unified 3.30.0), which picks a tier and returns
the tier's candidates. Each candidate is a harness profile and a model.

run_harness is the design's run service run activity, for one candidate. Its
Lease step starts the run's per-run sandbox: a Docker container of the
candidate's harness profile image, named after the run id
(run_task/sandbox.py). Its Start step sends POST /run to the harness server in
it, the Wait step follows GET /events until run_finished, and the Collect step
reads GET /usage, revokes the run grant and removes the container. pi runs in
the container, so the run keeps going when this worker dies, and the next
attempt finds the same container.

The workflow names the run id: the workflow id and the number of the
candidate it is trying. Every Temporal retry of run_harness for that
candidate sends the same POST /run, gets the run already in progress, and
reads its events from the last event id it heartbeated. It never starts pi a
second time.

Before POST /run, the activity asks the HTTP Gateway for a run grant: the
candidate's engine and model, the spend limit the workflow gave this run, and
an expiry. The harness server gets only the grant key. A worker that stops
mid-run leaves the grant live, because the run keeps going and the next
attempt re-attaches to it. That attempt asks for the grant again and gets a
new key for the same grant.

For a repo task, the Lease step first clones the task's repo into the run's
own workspace on this machine (run_task/code_host.py), and once a run
finishes, the publish activity commits pi's changes to a branch, pushes it
and opens the pull request. Code-host access stays on this machine: the
per-run sandbox holds no code-host token and no SSH key.

run_harness returns a result for every run that ended, finished or not, and
the workflow's Decide step reads its end reason. It raises only when it could
not follow the run to its end. When Temporal stops retrying it, the workflow
calls cleanup_run, which revokes the grant and removes the per-run sandbox.
"""

import asyncio
import re
from pathlib import Path
from string import Template
from typing import Any

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from run_task import code_host, harness_client, routing, run_grants, sandbox
from run_task.shared import (
    INLINE_TEMPLATE,
    CleanupResult,
    HarnessRequest,
    HarnessResult,
    PreparedRun,
    PublishRequest,
    PublishResult,
    RouteRequest,
    RouteResult,
    RunTaskInput,
    ref_for,
)

SAMPLE_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = SAMPLE_DIR / "prompts"
REPO_TEMPLATE = "repo-task-v1"

# The grant outlives the run's own time limit by this much, so it never expires under a run still ending.
GRANT_GRACE_SECONDS = 300


@activity.defn
async def prepare(request: RunTaskInput) -> PreparedRun:
    """Uses the task text the caller gave, or builds the prompt from a versioned template file.

    A pinned task's pair is checked here, so a bad pin fails before any per-run sandbox starts.
    """
    info = activity.info()
    pinned = None
    if request.pin:
        try:
            pinned = routing.pinned(request.pin)
        except routing.CandidateError as err:
            raise ApplicationError(str(err), type="HarnessConfigError", non_retryable=True) from err
    if request.repo or request.ticket:
        return await _prepare_repo_task(request, pinned)
    if request.task:
        activity.logger.info("prepare attempt %d: using the task text from the caller", info.attempt)
        return PreparedRun(template=INLINE_TEMPLATE, prompt=request.task.strip(), pinned=pinned)
    path = PROMPTS_DIR / f"{request.template}.txt"
    if not path.is_file():
        raise ApplicationError(f"no prompt template {path.name}", type="UnknownTemplate", non_retryable=True)
    prompt = path.read_text(encoding="utf-8").strip()
    activity.logger.info("prepare attempt %d: read the prompt from prompts/%s", info.attempt, path.name)
    return PreparedRun(template=request.template, prompt=prompt, pinned=pinned)


async def _prepare_repo_task(request: RunTaskInput, pinned: Any) -> PreparedRun:
    """The repo-task prompt: the ticket's or the caller's task, the repo, and the acceptance checks."""
    try:
        ticket = await code_host.read_ticket(request.ticket) if request.ticket else None
        repo = request.repo or (ticket.repo if ticket else "")
        code_host.check_repo(repo)
    except code_host.CodeHostError as err:
        raise ApplicationError(str(err), type="HarnessConfigError", non_retryable=not err.retryable) from err
    if ticket:
        title, description = ticket.title, ticket.body.strip() or "(the ticket has no description)"
        checks = ticket.checks or request.checks
    else:
        text = request.task.strip()
        if not text:
            raise ApplicationError("a repo task needs TASK or TICKET", type="HarnessConfigError", non_retryable=True)
        description, checks = text, request.checks
        title = _title_from(text)
    listed = (
        "\n".join(f"- {check}" for check in checks) or "- None were given. Run the repository's tests, if it has them."
    )
    template = (PROMPTS_DIR / f"{REPO_TEMPLATE}.txt").read_text(encoding="utf-8")
    prompt = Template(template).safe_substitute(repo=repo, title=title, description=description, checks=listed)
    activity.logger.info(
        "prepare: a repo task in %s%s, %d acceptance checks",
        repo,
        f" from ticket {ticket.ref}" if ticket else "",
        len(checks),
    )
    return PreparedRun(
        template=REPO_TEMPLATE,
        prompt=prompt.strip(),
        pinned=pinned,
        repo=repo,
        ticket=ticket.ref if ticket else "",
        title=title[:200],
    )


def _title_from(text: str, limit: int = 72) -> str:
    """A pull request title from task text: its first sentence, cut at a word under the limit."""
    first = re.split(r"(?<=[.!?])\s|\n", text.strip(), maxsplit=1)[0].strip().rstrip(".")
    if len(first) <= limit:
        return first
    return first[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "..."


@activity.defn
async def route(request: RouteRequest) -> RouteResult:
    """Asks the ai-api-unified router for the task's tier and the tier's candidates.

    The classifier call, when a classifier is configured, is the only model call
    this activity makes. No candidate is built or called.
    """
    options = {
        key: value for key, value in (("route_floor", request.floor), ("route_ceiling", request.ceiling)) if value
    }
    router = routing.router()
    try:
        decision = await router.aclassify_route(request.prompt, provider_options=options or None)
        candidates = [routing.candidate(c.engine, c.model) for c in decision.candidates]
    except ValueError as err:
        # An unknown tier in a hint, or a candidate the worker's startup check would have refused.
        raise ApplicationError(str(err), type="HarnessConfigError", non_retryable=True) from err
    config = router.config
    activity.logger.info(
        "route: tier %s (%s%s), %d candidates, classifier $%s",
        decision.tier,
        decision.method,
        f", confidence {decision.confidence:.2f}" if decision.confidence is not None else "",
        len(candidates),
        decision.classifier_usd,
    )
    return RouteResult(
        tier=str(decision.tier),
        method=decision.method,
        confidence=decision.confidence,
        candidates=candidates,
        tiers=[tier.name for tier in config.tiers],
        on_exhausted=config.on_exhausted,
        classifier_usd=str(decision.classifier_usd),
        config_id=decision.config_id,
    )


@activity.defn
async def run_harness(request: HarnessRequest) -> HarnessResult:
    """Starts or re-attaches to the run on the harness server and follows it to the end."""
    info = activity.info()
    run_id = request.run_id
    tag = f"[{run_id} attempt {info.attempt}]"
    progress = _progress_from(info.heartbeat_details)
    activity.logger.info(
        "run_harness attempt %d started for %s on %s, heartbeat timeout %ss",
        info.attempt,
        run_id,
        request.candidate.label,
        int(info.heartbeat_timeout.total_seconds()) if info.heartbeat_timeout else 0,
    )
    if not run_grants.gateway_key():
        raise ApplicationError(
            "set GATEWAY_API_KEY in the worker's environment", type="HarnessConfigError", non_retryable=True
        )

    ref = ref_for(info.workflow_id)
    image = str(request.candidate.profile["image"])
    if request.repo:
        await _clone(tag, request.repo, sandbox.workspace_for(ref, run_id))
    box = await _lease(tag, run_id, ref, image)
    try:
        grant_key = await _issue_grant(tag, request, info.workflow_id)
        async with harness_client.client(box) as client:
            reattached = await _start_or_reattach(client, tag, request, grant_key, progress, box.url)
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
        # No later attempt will re-attach, so nothing needs the per-run sandbox or the grant.
        if err.non_retryable:
            await _revoke_grant(tag, run_id)
            await _release(tag, run_id, ref)
        raise

    # The run has ended, so its grant key and its per-run sandbox have nothing left to do.
    grant_spent = await _revoke_grant(tag, run_id)
    await _release(tag, run_id, ref)
    data = finished["data"]
    activity.logger.info(
        "run_harness attempt %d: run %s ended %s (%s), %d heartbeats sent, returning the result to Temporal",
        info.attempt,
        run_id,
        data["status"],
        data["end_reason"],
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
        error=data.get("error"),
    )


@activity.defn
async def publish(request: PublishRequest) -> PublishResult:
    """Commits pi's changes to the branch mh/<REF>, pushes it, and opens the pull request.

    Returns an empty pr_url when pi changed nothing.
    """
    ref = ref_for(activity.info().workflow_id)
    workspace = sandbox.workspace_for(ref, request.run_id)
    body = _pr_body(request)
    message = f"{request.title}\n\nRun {request.run_id} on {request.candidate}." + (
        f"\n\nCloses {request.ticket}" if request.ticket else ""
    )
    try:
        published = await code_host.publish(
            repo=request.repo,
            workspace=workspace,
            branch=f"mh/{ref}",
            title=request.title,
            body=body,
            commit_message=message,
            pr_record=sandbox.task_dir_for(ref) / "pull-request.md",
        )
    except code_host.CodeHostError as err:
        raise ApplicationError(str(err), type="CodeHostError", non_retryable=not err.retryable) from err
    if published is None:
        print(f"[{request.run_id} publish] pi changed nothing in {workspace}, so there is no pull request", flush=True)
        return PublishResult(branch="", base="", commit="", files=[], pr_url="", created=False)
    print(
        f"[{request.run_id} publish] {', '.join(published.files)} on branch {published.branch} at "
        f"{published.commit[:12]}, pull request {'opened' if published.created else 'already open'}: {published.pr_url}",
        flush=True,
    )
    return PublishResult(
        branch=published.branch,
        base=published.base,
        commit=published.commit,
        files=published.files,
        pr_url=published.pr_url,
        created=published.created,
    )


def _pr_body(request: PublishRequest) -> str:
    lines = []
    if request.ticket:
        lines += [f"Closes {request.ticket}", ""]
    lines += ["## Summary from the run", "", request.summary or "(the run left no summary)", ""]
    lines += [
        "## Run",
        "",
        f"- Run: `{request.run_id}`",
        f"- Harness profile and model: `{request.candidate}`",
        f"- Model spend for the task so far: ${request.spent_usd}",
        "",
        (
            "Opened by the meta-harness. pi worked in a clone of this repo inside a per-run sandbox that holds "
            "no code-host credentials, and the meta-harness committed and pushed its changes."
        ),
    ]
    return "\n".join(lines)


@activity.defn
async def cleanup_run(run_id: str) -> CleanupResult:
    """Closes a run run_harness gave up on: revokes its grant and removes its per-run sandbox.

    Returns what the HTTP Gateway counted against the grant, so the workflow
    counts a failed run's spend too.
    """
    ref = ref_for(activity.info().workflow_id)
    tag = f"[{run_id} cleanup]"
    spent = await _revoke_grant(tag, run_id)
    await _release(tag, run_id, ref)
    return CleanupResult(grant_spent_usd=spent)


async def _clone(tag: str, repo: str, workspace: Path) -> None:
    """The repo task's clone, made on this machine before the per-run sandbox starts."""
    existed = (workspace / ".git").is_dir()
    try:
        branch = await code_host.clone(repo, workspace)
    except code_host.CodeHostError as err:
        raise ApplicationError(str(err), type="CodeHostError", non_retryable=not err.retryable) from err
    if not existed:
        print(f"{tag} cloned {repo} ({branch}) into {workspace}", flush=True)


async def _lease(tag: str, run_id: str, ref: str, image: str) -> sandbox.Sandbox:
    """The Lease step: the run's per-run sandbox, started now or found from an earlier attempt."""
    try:
        box = await sandbox.ensure(run_id, ref, image)
    except sandbox.SandboxError as err:
        raise ApplicationError(str(err), type="SandboxUnavailable", non_retryable=True) from err
    if box.created:
        print(
            f"{tag} per-run sandbox {box.name} started from {image} on its own network "
            f"{sandbox.network_name(run_id)}, out only through the egress proxy. "
            f"Workspace {sandbox.workspace_for(ref, run_id)} mounted at /workspace",
            flush=True,
        )
    else:
        print(f"{tag} per-run sandbox {box.name} is still running from an earlier attempt", flush=True)
    return box


async def _release(tag: str, run_id: str, ref: str) -> None:
    """Saves the harness server's log and removes the run's per-run sandbox."""
    if not await sandbox.destroy(run_id, ref):
        return
    print(
        f"{tag} per-run sandbox {sandbox.container_name(run_id)} and its network removed, "
        f"harness server log: {sandbox.log_path_for(ref, run_id)}",
        flush=True,
    )


async def _issue_grant(tag: str, request: HarnessRequest, workflow_id: str) -> str:
    """Asks the HTTP Gateway for the run's grant and returns its key."""
    profile = request.candidate.profile
    try:
        grant = await run_grants.issue(
            run_id=request.run_id,
            engine=request.candidate.engine,
            model=request.candidate.model,
            spend_limit_usd=request.spend_limit_usd,
            ttl_seconds=int(profile["max_minutes"]) * 60 + GRANT_GRACE_SECONDS,
            workflow_id=workflow_id,
            tags={"profile": request.candidate.profile_name},
        )
    except run_grants.RunGrantError as err:
        # A refusal is the request's fault and retrying repeats it. No answer, or a 503, may pass.
        if err.status in (400, 401, 409, 422):
            raise ApplicationError(str(err), type="HarnessConfigError", non_retryable=True) from err
        raise ApplicationError(str(err), type="GatewayUnavailable") from err
    if grant["created"]:
        print(
            f"{tag} run grant issued for {request.run_id}: {grant['engine']}:{grant['model']}, "
            f"spend limit ${grant['spend_limit_usd']}, expires {grant['expires_at']}",
            flush=True,
        )
    else:
        print(
            f"{tag} run grant for {request.run_id} is still live (spent ${grant['spent_usd']} of "
            f"${grant['spend_limit_usd']}). The HTTP Gateway added a key to it",
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
    if revoked.get("revoked"):
        print(f"{tag} run grant revoked: the HTTP Gateway counted ${revoked['spent_usd']} against it", flush=True)
    return str(revoked["spent_usd"])


async def _start_or_reattach(
    client: httpx.AsyncClient,
    tag: str,
    request: HarnessRequest,
    grant_key: str,
    progress: dict[str, Any],
    harness_url: str,
) -> bool:
    """POST /run. Returns True when the run already existed, so this attempt re-attached to it."""
    profile = request.candidate.profile
    body = {
        "run_id": request.run_id,
        # Each candidate is a new pi session: another model starts from the task, not from a transcript.
        # A session id names a directory, so it has no ":".
        "harness_session_id": request.run_id.replace(":", "."),
        "prompt": request.prompt,
        "profile": {key: profile[key] for key in ("name", "tools", "max_turns", "max_minutes")},
        # The grant decides the engine, the model and the spend limit. The harness server sees only the key.
        "grant_key": grant_key,
    }
    try:
        response = await client.post("/run", json=body)
    except httpx.HTTPError as err:
        raise ApplicationError(f"no harness server at {harness_url}: {err}", type="HarnessUnavailable") from err
    if response.status_code == 202:
        print(f"{tag} POST /run started run {request.run_id} on {request.candidate.label}", flush=True)
        return False
    if response.status_code == 200:
        status = response.json()["status"]
        print(
            f"{tag} POST /run: run {request.run_id} already exists ({status}). "
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
        progress["answer"] = data["text"].strip()
