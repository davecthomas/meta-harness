"""The Docker executor backend: one per-run sandbox container per run.

The technical design's per-run sandbox (section 8.1) on Docker, the executor
backend for local development (section 8.3). With no pool manager, the run
activity starts the per-run sandbox itself in its Lease step, and removes it
once the run has ended (section 6.3).

A per-run sandbox is a container of the harness profile image, named after the
run id. A retried activity finds the container its earlier attempt started and
re-attaches to the run inside it. Each container gets its own harness server
bearer token, the way a pool manager lease hands one out per lease. The token
lives only in the container's environment, so it stays out of Temporal's
workflow history and out of process listings.

pi's tools reach the host only through the run's workspace directory, mounted
at /workspace. The container holds no model provider key: pi's model calls go
to the HTTP Gateway on this machine with the run's grant key.
"""

import asyncio
import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

import httpx

SAMPLE_DIR = Path(__file__).resolve().parent.parent
IMAGE = os.environ.get("HARNESS_IMAGE", "harness-pi:dev")
# The HTTP Gateway as a container sees it. host.docker.internal is this machine.
SANDBOX_GATEWAY_URL = os.environ.get("SANDBOX_GATEWAY_URL", "http://host.docker.internal:8080")
RUNS_DIR = Path(os.environ.get("RUNS_DIR") or SAMPLE_DIR / "runs").resolve()
HEARTBEAT_SECONDS = os.environ.get("SANDBOX_HEARTBEAT_SECONDS", "2")
READY_SECONDS = float(os.environ.get("SANDBOX_READY_SECONDS", "60"))
# Every container this sample starts carries this label, so `make down` can find leftovers.
SAMPLE_LABEL = "meta-harness.sample"
CONTAINER_PORT = 8090


class SandboxError(Exception):
    """The per-run sandbox could not start, or its harness server is gone."""


@dataclass
class Sandbox:
    name: str
    url: str
    token: str
    # False when an earlier attempt started the container and this one found it.
    created: bool


def container_name(run_id: str) -> str:
    return "mh-sandbox-" + re.sub(r"[^A-Za-z0-9_.-]", "-", run_id)


def workspace_for(ref: str) -> Path:
    return RUNS_DIR / ref / "workspace"


def log_path_for(ref: str) -> Path:
    return RUNS_DIR / ref / "harness-server.log"


async def find(run_id: str) -> Sandbox | None:
    """The run's per-run sandbox when its container is running, else None.

    Raises SandboxError when the container exists but its harness server has exited.
    """
    name = container_name(run_id)
    code, out, _ = await _docker("inspect", name)
    if code != 0:
        return None
    info = json.loads(out)[0]
    if not info["State"]["Running"]:
        raise SandboxError(f"the harness server in {name} exited with code {info['State']['ExitCode']}")
    env = dict(item.split("=", 1) for item in info["Config"]["Env"] if "=" in item)
    ports = (info["NetworkSettings"]["Ports"] or {}).get(f"{CONTAINER_PORT}/tcp") or []
    if not ports:
        raise SandboxError(f"{name} publishes no port for the harness server")
    return Sandbox(
        name=name,
        url=f"http://127.0.0.1:{ports[0]['HostPort']}",
        token=env.get("HARNESS_SERVER_TOKEN", ""),
        created=False,
    )


async def ensure(run_id: str, ref: str) -> Sandbox:
    """Returns the run's running per-run sandbox, starting one when there is none.

    Waits until the harness server's /health is green, which includes reaching the HTTP Gateway.
    """
    existing = await find(run_id)
    if existing:
        return existing
    name = container_name(run_id)
    workspace = workspace_for(ref)
    workspace.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    args = [
        "run", "--detach", "--name", name,
        "--label", f"{SAMPLE_LABEL}=1",
        "--label", f"meta-harness.run_id={run_id}",
        "--label", f"meta-harness.ref={ref}",
        "--add-host=host.docker.internal:host-gateway",
        "--publish", f"127.0.0.1::{CONTAINER_PORT}",
        "--volume", f"{workspace}:/workspace",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--cpus", "2", "--memory", "4g", "--pids-limit", "512",
        # A bare -e NAME copies the value from the docker command's own environment, set below.
        "--env", "HARNESS_SERVER_TOKEN",
        "--env", f"GATEWAY_URL={SANDBOX_GATEWAY_URL}",
        "--env", f"HEARTBEAT_SECONDS={HEARTBEAT_SECONDS}",
        IMAGE,
    ]  # fmt: skip
    code, _, err = await _docker(*args, env={"HARNESS_SERVER_TOKEN": token})
    if code != 0:
        # Another attempt may have started it a moment ago.
        existing = await find(run_id)
        if existing:
            return existing
        raise SandboxError(f"docker run {name} failed: {err.strip()}")
    sandbox = await find(run_id)
    if sandbox is None:
        raise SandboxError(f"{name} stopped right after it started")
    sandbox.created = True
    await _wait_ready(sandbox, ref)
    return sandbox


async def destroy(run_id: str, ref: str) -> None:
    """Saves the harness server's log to runs/<ref>/ and removes the container."""
    name = container_name(run_id)
    await save_log(name, ref)
    await _docker("rm", "--force", name)


async def save_log(name: str, ref: str) -> None:
    code, out, err = await _docker("logs", name)
    if code == 0:
        path = log_path_for(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(out + err, encoding="utf-8")


async def _wait_ready(sandbox: Sandbox, ref: str) -> None:
    deadline = asyncio.get_running_loop().time() + READY_SECONDS
    last = "no answer"
    async with httpx.AsyncClient(base_url=sandbox.url, headers={"authorization": f"Bearer {sandbox.token}"}) as http:
        while asyncio.get_running_loop().time() < deadline:
            try:
                response = await http.get("/health", timeout=5.0)
                if response.status_code == 200:
                    return
                last = response.text
            except httpx.HTTPError as err:
                last = str(err)
            await asyncio.sleep(0.5)
    await save_log(sandbox.name, ref)
    await _docker("rm", "--force", sandbox.name)
    raise SandboxError(f"the harness server in {sandbox.name} was not ready within {READY_SECONDS:.0f}s: {last}")


async def _docker(*args: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **env} if env else None,
        )
    except FileNotFoundError as err:
        raise SandboxError("the docker command is not installed") from err
    out, err = await process.communicate()
    return process.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
