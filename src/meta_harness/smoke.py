"""The deploy's smoke task: one small task through the deployed system, so a deploy that broke task runs fails.

    mh deploy smoke

A deploy can report success and still have broken what the system is for: on 2026-11-10 a change started
every sandbox under the wrong container name, and nothing noticed, because no task happened to run. Tests and
the zero-cost end-to-end check use fakes for the model, the code host and the VM; only the deployed system
shows whether a task runs there.

So after the VM reports the commit, the deploy submits one task through the Task API, as a caller would: write
a one-line file in a scratch repo on the VM, pinned to the first candidate of the router's first tier (the
cheapest), with a small spend limit. The scheduler admits it, the RunTask workflow runs it in a per-run
sandbox against the real HTTP Gateway, and it ends closed. Its type is `smoke`: it opens no pull request,
gets no review, and the console's lists leave it out. A task that does not end closed within the wait is
tried once more, since one model call can fail by chance; two failures fail the deploy.

Nothing is tested while the kill switch is on or the scheduler drains: no task may start then, by an
operator's choice, and that is not the deploy's failure.
"""

import os
import subprocess
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

PROMPT = "Create a file named smoke.txt containing exactly one line: ok. Then stop."
# Enough for a few turns on the cheapest model, and no more.
SPEND_LIMIT_USD = Decimal("0.25")
WAIT_SECONDS = 600
POLL_SECONDS = 5
ENDED = {"closed", "failed", "canceled", "ready", "review_unresolved"}


def scratch_repo(path: Path) -> Path:
    """The bare repo the smoke task works in, with one commit on main, made if it is not there. It stands in
    for a code host: the task clones it, and pushes nothing back."""
    if (path / "HEAD").is_file():
        return path
    seed = path.with_suffix(".seed")
    path.parent.mkdir(parents=True, exist_ok=True)

    def git(*args: str, cwd: Path | None = None) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    git("init", "--quiet", "--bare", "--initial-branch=main", str(path))
    git("init", "--quiet", "--initial-branch=main", str(seed))
    (seed / "README.md").write_text("# smoke\n\nThe repo a deploy's smoke task works in.\n", encoding="utf-8")
    git("add", "README.md", cwd=seed)
    git(
        "-c",
        "user.name=meta-harness",
        "-c",
        "user.email=meta-harness@localhost",
        "commit",
        "--quiet",
        "-m",
        "first commit",
        cwd=seed,
    )
    git("push", "--quiet", str(path), "main", cwd=seed)
    return path


def cheapest_candidate(tiers: dict[str, Any]) -> str:
    """The first candidate of the router's first tier: tiers are listed cheapest first."""
    listed = tiers.get("tiers") or []
    if not listed or not listed[0].get("models"):
        raise RuntimeError("the router's first tier has no candidate to run the smoke task on")
    return str(listed[0]["models"][0])


def run_once(
    tasks: Any, repo: str, pin: str, commit: str, say: Callable[[str], None], wait: float = WAIT_SECONDS
) -> tuple[bool, str]:
    """Submits one smoke task and waits for it to end. Returns (whether it ended closed, what happened)."""
    task = tasks.create(
        external_ref=f"smoke:{commit[:12]}:{uuid.uuid4().hex[:8]}",
        repo=repo,
        task_type="smoke",
        # Ahead of the queue: the deploy is waiting on it.
        priority=1000,
        title=f"Deploy smoke task for {commit[:12]}",
        description=PROMPT,
        acceptance_checks=[],
        pin=pin,
        max_usd=str(SPEND_LIMIT_USD),
    )
    say(f"smoke task {task['id']} on {pin}")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        task = tasks.get(task["id"])
        if task["status"] in ENDED:
            reason = task.get("status_reason") or "no reason was recorded"
            return task["status"] == "closed", f"task {task['id']} ended {task['status']}: {reason}"
        time.sleep(POLL_SECONDS)
    try:
        tasks.cancel(task["id"], "the deploy's smoke task did not end in time")
    except Exception:  # noqa: BLE001 - the timeout is the result either way
        pass
    return (
        False,
        f"task {task['id']} was still {task['status']} after {int(wait)} seconds: {task.get('status_reason') or ''}",
    )


def run(
    tasks: Any, tiers: dict[str, Any], repo_path: Path, commit: str, say: Callable[[str], None], held: str = ""
) -> int:
    """The smoke check, tried twice. Returns the exit code: 0 when a task ended closed, or none may start."""
    if held:
        say(f"no smoke task: {held}, so no task may start")
        return 0
    repo = str(scratch_repo(repo_path))
    pin = cheapest_candidate(tiers)
    for attempt in (1, 2):
        ok, what = run_once(tasks, repo, pin, commit, say)
        say(f"{'passed' if ok else 'failed'}: {what}")
        if ok:
            return 0
        if attempt == 1:
            say("trying once more: one failure can be chance")
    return 1


def main(say: Callable[[str], None] = print) -> int:
    """`mh deploy smoke`, on the deployment's own machine: its Task API, its configuration, its kill switch."""
    from meta_harness.config import store
    from meta_harness.scheduler.admission import kill_switch_state
    from meta_harness.tasks.client import TaskClient

    engaged, draining = kill_switch_state()
    held = "the kill switch is on" if engaged else "the scheduler is draining" if draining else ""
    commit = os.environ.get("MH_DEPLOYED_COMMIT") or "unknown"
    repo_path = Path(os.environ.get("MH_SMOKE_REPO", "/var/lib/meta-harness/smoke/origin.git"))
    tasks = TaskClient(api_key=os.environ.get("MH_SERVICE_KEY", ""))
    return run(tasks, store.current_document("tiers"), repo_path, commit, say, held)
