"""The Docker executor backend: one per-run sandbox container per run.

The technical design's per-run sandbox (section 8.1) on Docker, the executor
backend for local development (section 8.3). With no pool manager, the run
activity starts the per-run sandbox itself in its Lease step, and removes it
once the run has ended (section 6.3).

A per-run sandbox is a container of the run's harness profile image, named
after the run id. Images are built locally and never pulled, so a harness
profile naming a missing image fails at once. A retried activity finds the container its earlier attempt started and
re-attaches to the run inside it. Each container gets its own harness server
bearer token, the way a pool manager lease hands one out per lease. The token
lives only in the container's environment, so it stays out of Temporal's
workflow history and out of process listings.

pi's tools reach the host only through the run's workspace directory, mounted
at /workspace, and the clone's git directory beside it, mounted read-only at
its own path (git_dir_for). The container holds no model provider key: pi's model calls go
to the HTTP Gateway on this machine with the run's grant key.

Network: each per-run sandbox has its own internal Docker network, with no
route out and no other per-run sandbox on it. The local egress proxy
(egress-proxy/, which stack.sh starts) joins that network. The container's
only way out is the proxy's egress listener, which allows only the HTTP
Gateway, the code host and package registries. The run activity reaches the
harness server through the proxy's control listener on 127.0.0.1, because
Docker publishes no port from an internal network.
"""

import asyncio
import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

import httpx

from meta_harness.run_service.paths import REPO_ROOT

IMAGE = os.environ.get("HARNESS_IMAGE", "harness-pi:dev")
# The HTTP Gateway as the egress proxy reaches it. host.docker.internal is this machine.
SANDBOX_GATEWAY_URL = os.environ.get("SANDBOX_GATEWAY_URL", "http://host.docker.internal:8080")
RUNS_DIR = Path(os.environ.get("RUNS_DIR") or REPO_ROOT / "runs").resolve()
HEARTBEAT_SECONDS = os.environ.get("SANDBOX_HEARTBEAT_SECONDS", "2")
READY_SECONDS = float(os.environ.get("SANDBOX_READY_SECONDS", "60"))
# Every container and network this sample starts carries this label, so `make down` can find leftovers.
SAMPLE_LABEL = "meta-harness.sample"
CONTAINER_PORT = 8090
EGRESS_PROXY = os.environ.get("EGRESS_PROXY_CONTAINER", "mh-egress-proxy")
EGRESS_PORT = 3128
CONTROL_PORT = 8091


class SandboxError(Exception):
    """The per-run sandbox could not start, or its harness server is gone."""


@dataclass
class Sandbox:
    name: str
    # The harness server as the egress proxy's control listener reaches it.
    url: str
    token: str
    # The control listener, on this machine's 127.0.0.1. Every request to url goes through it.
    proxy: str
    # False when an earlier attempt started the container and this one found it.
    created: bool


def _suffix(run_id: str) -> str:
    """A DNS-safe name part for the run: readable, at most 52 characters, unique by a short hash."""
    slug = re.sub(r"[^a-z0-9-]+", "-", run_id.lower()).strip("-")[:43].rstrip("-")
    return f"{slug}-{hashlib.sha256(run_id.encode()).hexdigest()[:8]}"


def container_name(run_id: str) -> str:
    return f"mh-sandbox-{_suffix(run_id)}"


def network_name(run_id: str) -> str:
    return f"mh-run-{_suffix(run_id)}"


def task_dir_for(ref: str) -> Path:
    """runs/<ref>: everything a task leaves behind."""
    return RUNS_DIR / ref


def workspace_for(ref: str, run_id: str) -> Path:
    """Each run gets its own workspace, so a candidate never starts from another candidate's edits."""
    return RUNS_DIR / ref / run_id.rsplit(":", 1)[-1] / "workspace"


def log_path_for(ref: str, run_id: str) -> Path:
    """One harness server log per run, beside its workspace: runs/<ref>/run-1/harness-server.log."""
    return RUNS_DIR / ref / run_id.rsplit(":", 1)[-1] / "harness-server.log"


def git_dir_for(ref: str, run_id: str) -> Path:
    """The clone's git directory, beside the workspace and outside it: runs/<ref>/run-1/git.

    The per-run sandbox mounts it read-only at this same path, so pi's git
    commands in /workspace find it through the workspace's `.git` file, and
    nothing pi does can change a hook or a setting the host's git would run.
    """
    return RUNS_DIR / ref / run_id.rsplit(":", 1)[-1] / "git"


def run_args(
    *, name: str, network: str, run_id: str, ref: str, workspace: Path, git_dir: Path, image: str
) -> list[str]:
    """The `docker run` arguments for a run's per-run sandbox."""
    egress = f"http://{EGRESS_PROXY}:{EGRESS_PORT}"
    volumes = ["--volume", f"{workspace}:/workspace"]
    if git_dir.is_dir():
        volumes += ["--volume", f"{git_dir}:{git_dir}:ro"]
    return [
        "run", "--detach", "--pull", "never", "--name", name, "--hostname", name,
        "--network", network,
        "--label", f"{SAMPLE_LABEL}=1",
        "--label", f"meta-harness.run_id={run_id}",
        "--label", f"meta-harness.ref={ref}",
        *volumes,
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--cpus", "2", "--memory", "4g", "--pids-limit", "512",
        # A bare -e NAME copies the value from the docker command's own environment, set by the caller.
        "--env", "HARNESS_SERVER_TOKEN",
        "--env", f"GATEWAY_URL={SANDBOX_GATEWAY_URL}",
        "--env", f"HEARTBEAT_SECONDS={HEARTBEAT_SECONDS}",
        # Everything leaves through the egress proxy. Node's fetch uses these only with NODE_USE_ENV_PROXY.
        "--env", f"HTTP_PROXY={egress}", "--env", f"HTTPS_PROXY={egress}",
        "--env", f"http_proxy={egress}", "--env", f"https_proxy={egress}",
        "--env", "NO_PROXY=localhost,127.0.0.1", "--env", "no_proxy=localhost,127.0.0.1",
        "--env", "NODE_USE_ENV_PROXY=1",
        "--env", "NODE_OPTIONS=--disable-warning=UNDICI-EHPA",
        image,
    ]  # fmt: skip


async def control_proxy() -> str:
    """The egress proxy's control listener as this machine reaches it."""
    code, out, _ = await _docker("port", EGRESS_PROXY, f"{CONTROL_PORT}/tcp")
    if code != 0 or not out.strip():
        raise SandboxError(
            f"the egress proxy container {EGRESS_PROXY} is not running. `make run` and `make up` start it."
        )
    host_port = out.strip().splitlines()[0].rsplit(":", 1)[1]
    return f"http://127.0.0.1:{host_port}"


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
    return Sandbox(
        name=name,
        url=f"http://{name}:{CONTAINER_PORT}",
        token=env.get("HARNESS_SERVER_TOKEN", ""),
        proxy=await control_proxy(),
        created=False,
    )


async def ensure(run_id: str, ref: str, image: str = IMAGE) -> Sandbox:
    """Returns the run's running per-run sandbox, starting one when there is none.

    Waits until the harness server's /health is green, which includes reaching
    the HTTP Gateway through the egress proxy.
    """
    existing = await find(run_id)
    if existing:
        return existing
    proxy = await control_proxy()
    name = container_name(run_id)
    network = network_name(run_id)
    await _ensure_network(network, run_id, ref)
    workspace = workspace_for(ref, run_id)
    workspace.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    args = run_args(
        name=name,
        network=network,
        run_id=run_id,
        ref=ref,
        workspace=workspace,
        git_dir=git_dir_for(ref, run_id),
        image=image,
    )
    code, _, err = await _docker(*args, env={"HARNESS_SERVER_TOKEN": token})
    if code != 0:
        # Another attempt may have started it a moment ago.
        existing = await find(run_id)
        if existing:
            return existing
        await _remove_network(network)
        raise SandboxError(f"docker run {name} failed: {err.strip()}")
    sandbox = Sandbox(name=name, url=f"http://{name}:{CONTAINER_PORT}", token=token, proxy=proxy, created=True)
    await _wait_ready(sandbox, run_id, ref)
    return sandbox


async def destroy(run_id: str, ref: str) -> bool:
    """Saves the harness server's log to runs/<ref>/, removes the container, and removes its network.

    Returns False when there was no container to remove.
    """
    name = container_name(run_id)
    existed = await save_log(name, ref, run_id)
    await _docker("rm", "--force", name)
    await _remove_network(network_name(run_id))
    return existed


async def save_log(name: str, ref: str, run_id: str) -> bool:
    code, out, err = await _docker("logs", name)
    if code == 0:
        path = log_path_for(ref, run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(out + err, encoding="utf-8")
    return code == 0


async def _ensure_network(network: str, run_id: str, ref: str) -> None:
    """The run's own internal network, with the egress proxy attached."""
    code, _, _ = await _docker("network", "inspect", network)
    if code != 0:
        code, _, err = await _docker(
            "network", "create", "--internal",
            "--label", f"{SAMPLE_LABEL}=1",
            "--label", f"meta-harness.run_id={run_id}",
            "--label", f"meta-harness.ref={ref}",
            network,
        )  # fmt: skip
        if code != 0 and "already exists" not in err:
            raise SandboxError(f"docker network create {network} failed: {err.strip()}")
    code, _, err = await _docker("network", "connect", network, EGRESS_PROXY)
    if code != 0 and "already exists" not in err:
        raise SandboxError(f"could not attach the egress proxy to {network}: {err.strip()}")


async def _remove_network(network: str) -> None:
    await _docker("network", "disconnect", "--force", network, EGRESS_PROXY)
    await _docker("network", "rm", network)


async def _wait_ready(sandbox: Sandbox, run_id: str, ref: str) -> None:
    deadline = asyncio.get_running_loop().time() + READY_SECONDS
    last = "no answer"
    async with httpx.AsyncClient(
        base_url=sandbox.url, headers={"authorization": f"Bearer {sandbox.token}"}, proxy=sandbox.proxy
    ) as http:
        while asyncio.get_running_loop().time() < deadline:
            try:
                response = await http.get("/health", timeout=5.0)
                if response.status_code == 200:
                    return
                last = response.text
            except httpx.HTTPError as err:
                last = str(err) or type(err).__name__
            await asyncio.sleep(0.5)
    await destroy(run_id, ref)
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
