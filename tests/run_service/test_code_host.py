"""The code host keeps the clone's git directory out of the per-run sandbox's reach.

The workspace is mounted into the sandbox read-write, so pi can write
anything under it, including a `.git` file that points at a git directory of
its own with a hook in it. The host's commit and push must never run what pi
wrote. These tests plant exactly that and publish.
"""

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from meta_harness.run_service import code_host


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """A bare repo with one commit on main, standing in for the code host."""
    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "--quiet", "--initial-branch=main", cwd=seed)
    (seed / "hello.txt").write_text("hello\n")
    _git("add", "hello.txt", cwd=seed)
    _git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "--quiet", "-m", "seed", cwd=seed)
    bare = tmp_path / "origin.git"
    _git("clone", "--quiet", "--bare", str(seed), str(bare), cwd=tmp_path)
    _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=bare)
    return bare


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    return tmp_path / "runs" / "task" / "run-1"


async def test_clone_keeps_the_git_directory_outside_the_workspace(origin: Path, run_dir: Path):
    workspace, git_dir = run_dir / "workspace", run_dir / "git"
    branch = await code_host.clone(str(origin), workspace, git_dir)

    assert branch == "main"
    assert (workspace / "hello.txt").read_text() == "hello\n"
    assert (workspace / ".git").is_file(), "the workspace holds only a pointer file, not the git directory"
    assert git_dir.is_dir() and (git_dir / "HEAD").is_file()
    assert git_dir not in workspace.parents and workspace not in git_dir.parents


async def test_clone_again_finds_the_earlier_clone(origin: Path, run_dir: Path):
    workspace, git_dir = run_dir / "workspace", run_dir / "git"
    await code_host.clone(str(origin), workspace, git_dir)
    (workspace / "hello.txt").write_text("edited by pi\n")

    assert await code_host.clone(str(origin), workspace, git_dir) == "main"
    assert (workspace / "hello.txt").read_text() == "edited by pi\n", "a retried attempt keeps pi's work"


async def test_publish_never_runs_what_pi_wrote_into_the_workspace(origin: Path, run_dir: Path, tmp_path: Path):
    """pi points the workspace's `.git` file at its own git directory, with a hook that would run on commit."""
    workspace, git_dir = run_dir / "workspace", run_dir / "git"
    await code_host.clone(str(origin), workspace, git_dir)
    marker = tmp_path / "pwned"
    evil = workspace / "evil-git"
    shutil.copytree(git_dir, evil)
    hooks = evil / "hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    _git("config", "core.fsmonitor", f"touch {marker}", cwd=evil)
    (workspace / ".git").write_text("gitdir: evil-git\n")
    (workspace / "hello.txt").write_text("changed\n")
    # Control: a git command that trusts the workspace would now use pi's directory.
    assert Path(_git("rev-parse", "--absolute-git-dir", cwd=workspace)).resolve() == evil.resolve()

    published = await code_host.publish(
        repo=str(origin),
        workspace=workspace,
        git_dir=git_dir,
        branch="mh/task",
        title="t",
        body="b",
        commit_message="m",
        pr_record=run_dir / "pull-request.md",
    )

    assert not marker.exists(), "the host ran a hook or fsmonitor command pi planted"
    assert published is not None and published.created
    assert "hello.txt" in published.files
    assert _git("show", "mh/task:hello.txt", cwd=origin) == "changed"
    assert _git("rev-parse", "mh/task", cwd=origin) == published.commit
    assert not list((git_dir / "hooks").glob("pre-commit")), "the real git directory took no hook"


async def test_publish_reports_nothing_when_pi_changed_nothing(origin: Path, run_dir: Path):
    workspace, git_dir = run_dir / "workspace", run_dir / "git"
    await code_host.clone(str(origin), workspace, git_dir)

    published = await code_host.publish(
        repo=str(origin),
        workspace=workspace,
        git_dir=git_dir,
        branch="mh/task",
        title="t",
        body="b",
        commit_message="m",
        pr_record=run_dir / "pull-request.md",
    )

    assert published is None


async def test_publish_again_finds_the_branch_and_the_record(origin: Path, run_dir: Path):
    workspace, git_dir = run_dir / "workspace", run_dir / "git"
    await code_host.clone(str(origin), workspace, git_dir)
    (workspace / "hello.txt").write_text("changed\n")
    kwargs = dict(
        repo=str(origin),
        workspace=workspace,
        git_dir=git_dir,
        branch="mh/task",
        title="t",
        body="b",
        commit_message="m",
        pr_record=run_dir / "pull-request.md",
    )

    first = await code_host.publish(**kwargs)
    second = await code_host.publish(**kwargs)

    assert first is not None and first.created
    assert second is not None and not second.created
    assert second.commit == first.commit
