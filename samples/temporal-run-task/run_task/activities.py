"""The two activities: prepare builds the prompt, run_harness runs pi.

run_harness stands in for the design's harness engine call: lease a sandbox,
POST /run, wait for the run to end. Here the "sandbox" is a local subprocess
running the pi model client sample (`npm start` in samples/pi-model-client).
"""

import asyncio
import os
import re
import signal
import subprocess
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError

from run_task.shared import HarnessRequest, HarnessResult, PreparedRun, RunTaskInput

SAMPLE_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = SAMPLE_DIR / "prompts"
PI_SAMPLE_DIR = Path(os.environ.get("PI_SAMPLE_DIR", SAMPLE_DIR.parent / "pi-model-client")).resolve()
NVM_DIR = os.environ.get("NVM_DIR", str(Path.home() / ".nvm"))

HEARTBEAT_EVERY_SECONDS = 1.0
STOP_GRACE_SECONDS = 2.0

# Switch to the Node version in .nvmrc, then replace bash with npm so the
# process group leader is npm itself.
PI_COMMAND = 'source "$NVM_DIR/nvm.sh" && nvm use >/dev/null && exec npm start'

RUN_LINE = re.compile(
    r"^\[run\] model turns=(?P<turns>\d+)\s+failed=(?P<failed>\d+)\s+"
    r"tokens in=(?P<tokens_in>\d+) out=(?P<tokens_out>\d+)\s+usd=(?P<usd>[\d.]+)"
)
FILE_LINE = re.compile(r"^\[workspace\] hello\.txt: (?P<content>.*)$")


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
    """Runs pi as a subprocess, heartbeats while it runs, and parses its final lines."""
    info = activity.info()
    tag = f"[run_harness attempt {info.attempt}]"
    activity.logger.info(
        "run_harness attempt %d started for %s, heartbeat timeout %ss",
        info.attempt,
        info.workflow_id,
        int(info.heartbeat_timeout.total_seconds()) if info.heartbeat_timeout else 0,
    )
    if not os.environ.get("GATEWAY_API_KEY"):
        raise ApplicationError(
            "set GATEWAY_API_KEY in the worker's environment", type="HarnessConfigError", non_retryable=True
        )

    _stop_previous_attempt(info.heartbeat_details, tag)

    env = dict(os.environ)
    env.update(NVM_DIR=NVM_DIR, SAMPLE_TASK=request.prompt, SAMPLE_ENGINE=request.engine, SAMPLE_MODEL=request.model)
    # A new session puts npm, tsx and node in one process group the activity can stop as a unit.
    proc = await asyncio.create_subprocess_exec(
        "bash",
        "-c",
        PI_COMMAND,
        cwd=PI_SAMPLE_DIR,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    print(f"{tag} started pi, process group {proc.pid}", flush=True)
    sent = [0]
    heartbeats = asyncio.create_task(_heartbeat_until_done(proc.pid, sent))
    lines: list[str] = []
    try:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            lines.append(line)
            print(f"{tag} {line}", flush=True)
        exit_code = await proc.wait()
    except asyncio.CancelledError:
        details = activity.cancellation_details()
        reason = _reasons(details) if details else "worker shutdown" if activity.is_worker_shutdown() else "unknown"
        print(f"{tag} cancellation received ({reason}), stopping pi", flush=True)
        raise
    finally:
        heartbeats.cancel()
        if proc.returncode is None:
            how = await _stop_group(proc)
            print(f"{tag} pi stopped {how}, exit code {proc.returncode}", flush=True)

    if exit_code == 2:
        raise ApplicationError(
            "pi sample rejected its configuration (exit 2)", type="HarnessConfigError", non_retryable=True
        )
    if exit_code != 0:
        raise ApplicationError(f"pi exited with code {exit_code}", type="HarnessFailed")
    result = _parse(lines, info.attempt, exit_code)
    activity.logger.info(
        "run_harness attempt %d: pi exited 0 after %d heartbeats, returning the result to Temporal",
        info.attempt,
        sent[0],
    )
    return result


async def _heartbeat_until_done(pgid: int, sent: list[int]) -> None:
    """Heartbeats while pi runs. The details carry pi's process group, so a retry can find it.

    sent[0] counts the heartbeats. Heartbeats are not history events, so the
    log is the only place they show.
    """
    while True:
        activity.heartbeat({"pgid": pgid})
        sent[0] += 1
        if sent[0] == 1:
            activity.logger.info(
                "run_harness attempt %d: first heartbeat sent, then one every %gs while pi runs",
                activity.info().attempt,
                HEARTBEAT_EVERY_SECONDS,
            )
        await asyncio.sleep(HEARTBEAT_EVERY_SECONDS)


async def _stop_group(proc: asyncio.subprocess.Process) -> str:
    """Sends SIGTERM to pi's process group, then SIGKILL if it is still running after a grace period.

    Under tsx, pi does not exit on SIGTERM (see the README's findings), so the
    SIGKILL step is the one that usually stops it. Returns how pi stopped.
    """
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(proc.wait(), STOP_GRACE_SECONDS)
        return "after SIGTERM"
    except TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
        return f"by SIGKILL, {STOP_GRACE_SECONDS:g}s after SIGTERM"


def _stop_previous_attempt(heartbeat_details: object, tag: str) -> None:
    """Stops a pi run left by an earlier attempt whose worker died.

    The sample cannot re-attach to that run's output, so it stops it and starts
    again. It checks that the process group leader is still npm before
    signaling, in case the operating system reused the id.
    """
    if not heartbeat_details:
        return
    last = heartbeat_details[-1] if isinstance(heartbeat_details, (list, tuple)) else None
    pgid = last.get("pgid") if isinstance(last, dict) else None
    if not isinstance(pgid, int):
        return
    leader = subprocess.run(
        ["ps", "-o", "command=", "-p", str(pgid)], check=False, capture_output=True, text=True
    ).stdout.strip()
    if not leader.startswith("npm"):
        print(f"{tag} previous attempt's pi (process group {pgid}) is gone", flush=True)
        return
    print(f"{tag} previous attempt's pi is still running (process group {pgid}), stopping it", flush=True)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _reasons(details: activity.ActivityCancellationDetails) -> str:
    flags = ("cancel_requested", "not_found", "paused", "reset", "timed_out", "worker_shutdown")
    return ",".join(name for name in flags if getattr(details, name, False))


def _parse(lines: list[str], attempt: int, exit_code: int) -> HarnessResult:
    run = next((m for m in map(RUN_LINE.match, lines) if m), None)
    if run is None:
        raise ApplicationError("pi finished without a [run] line", type="HarnessFailed")
    if int(run["turns"]) == int(run["failed"]):
        # The pi sample exits 0 even when no model turn got an answer.
        raise ApplicationError(f"every model turn failed ({run['failed']} of {run['turns']})", type="HarnessFailed")
    file = next((m for m in map(FILE_LINE.match, lines) if m), None)
    return HarnessResult(
        attempt=attempt,
        exit_code=exit_code,
        turns=int(run["turns"]),
        failed_turns=int(run["failed"]),
        tokens_in=int(run["tokens_in"]),
        tokens_out=int(run["tokens_out"]),
        usd=float(run["usd"]),
        file_content=file["content"] if file else None,
    )
