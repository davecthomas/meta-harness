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


async def test_a_clone_an_earlier_attempt_never_finished_is_made_again(origin: Path, run_dir: Path):
    """A worker restart can stop a clone between its steps. What it leaves has files missing, or is still on
    the default branch when the run is a review of a pull request's: a later attempt must not work in it."""
    _git("branch", "mh/t-1", cwd=origin)
    workspace, git_dir = run_dir / "workspace", run_dir / "git"
    await code_host.clone(str(origin), workspace, git_dir)
    # As the attempt left it: cloned, on main, a file not yet written, and no mark that it finished.
    (git_dir / code_host.CLONE_DONE).unlink()
    (workspace / "hello.txt").unlink()
    (workspace / "half-written.tmp").write_text("x")

    branch = await code_host.clone(str(origin), workspace, git_dir, branch="mh/t-1")

    assert branch == "mh/t-1", "the pull request's branch, not the default one the unfinished clone was on"
    assert (workspace / "hello.txt").read_text() == "hello\n" and not (workspace / "half-written.tmp").exists()
    assert (git_dir / code_host.CLONE_DONE).is_file()


async def test_a_local_repo_the_deployment_does_not_list_is_never_cloned_or_pushed_to(
    origin: Path, run_dir: Path, tmp_path: Path, monkeypatch
):
    """A push runs the destination repo's hooks on the host, so a path nobody vouched for is refused: at the
    check, at the clone, and at the push of a run that cloned while the path was still listed."""
    marker = tmp_path / "hook-ran"
    hook = origin / "hooks" / "pre-receive"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    workspace, git_dir = run_dir / "workspace", run_dir / "git"
    await code_host.clone(str(origin), workspace, git_dir)
    (workspace / "hello.txt").write_text("changed\n")
    monkeypatch.setenv("MH_LOCAL_REPO_ROOTS", str(tmp_path / "somewhere-else"))

    with pytest.raises(code_host.CodeHostError, match="MH_LOCAL_REPO_ROOTS") as refused:
        code_host.check_repo(str(origin))
    assert not refused.value.retryable
    with pytest.raises(code_host.CodeHostError, match="MH_LOCAL_REPO_ROOTS"):
        await code_host.clone(str(origin), tmp_path / "w2", tmp_path / "g2")
    with pytest.raises(code_host.CodeHostError, match="MH_LOCAL_REPO_ROOTS"):
        await code_host.publish(
            repo=str(origin),
            workspace=workspace,
            git_dir=git_dir,
            branch="mh/task",
            title="t",
            body="b",
            commit_message="m",
            pr_record=run_dir / "pull-request.md",
        )
    assert not marker.exists(), "the push ran the unlisted repo's hook on the host"
    assert not (tmp_path / "w2").exists()


async def test_a_push_the_code_host_refuses_for_a_workflow_file_ends_the_task_saying_who_makes_the_change(
    origin: Path, run_dir: Path, monkeypatch
):
    from meta_harness.supervisor import failures

    workspace, git_dir = run_dir / "workspace", run_dir / "git"
    await code_host.clone(str(origin), workspace, git_dir)
    (workspace / ".github" / "workflows").mkdir(parents=True)
    (workspace / ".github" / "workflows" / "deploy.yml").write_text("on: push\n")
    real = code_host._git

    async def refusing(git_dir, workspace, *args, env=None):
        if args and args[0] == "push":
            raise code_host.CodeHostError(
                "git push failed: ! [remote rejected] mh/task -> mh/task (refusing to allow a GitHub App to create "
                "or update workflow `.github/workflows/deploy.yml` without `workflows` permission)"
            )
        return await real(git_dir, workspace, *args, env=env)

    monkeypatch.setattr(code_host, "_git", refusing)

    with pytest.raises(code_host.CodeHostError) as refused:
        await code_host.publish(
            repo=str(origin),
            workspace=workspace,
            git_dir=git_dir,
            branch="mh/task",
            title="t",
            body="b",
            commit_message="m",
            pr_record=run_dir / "pull-request.md",
        )

    said = str(refused.value)
    assert not refused.value.retryable
    assert said.startswith("the run changed .github/workflows/deploy.yml, and Muxxy may not change a workflow")
    assert "A person makes this change. Cancel the task" in said
    assert failures.kind(f"CodeHostError: {said}") == failures.PERMANENT, "the Task Manager never retries it"


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


async def test_publish_refuses_a_workspace_with_a_repository_inside_it(origin: Path, run_dir: Path, tmp_path: Path):
    """pi makes a repository inside the workspace whose configuration names a command. git reads a nested
    repository's configuration when status or diff looks at it, so the host would run the command."""
    workspace, git_dir = run_dir / "workspace", run_dir / "git"
    await code_host.clone(str(origin), workspace, git_dir)
    marker = tmp_path / "pwned"
    sub = workspace / "sub"
    sub.mkdir()
    _git("init", "--quiet", cwd=sub)
    _git(
        "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "--quiet", "--allow-empty", "-m", "x", cwd=sub
    )
    _git("config", "core.fsmonitor", f"touch {marker}", cwd=sub)

    with pytest.raises(code_host.CodeHostError, match="git repository inside its workspace, at sub") as refused:
        await code_host.publish(
            repo=str(origin), workspace=workspace, git_dir=git_dir, branch="mh/task", title="t", body="b",
            commit_message="m", pr_record=run_dir / "pull-request.md",
        )  # fmt: skip
    assert not refused.value.retryable and "Retry the task" in str(refused.value)
    assert not marker.exists(), "the host ran the nested repository's file monitor command"

    # Should a nested repository get past the check, status and diff still do not look inside it.
    await code_host._run(
        "git", f"--git-dir={git_dir}", f"--work-tree={workspace}", "-c", "core.hooksPath=/dev/null", "add", "--all"
    )  # fmt: skip
    original, code_host.refuse_nested_repos = code_host.refuse_nested_repos, lambda workspace: None
    try:
        await code_host._git(git_dir, workspace, "status", "--porcelain")
        await code_host._git(git_dir, workspace, "diff", "HEAD")
    finally:
        code_host.refuse_nested_repos = original
    assert not marker.exists(), "status or diff ran the nested repository's file monitor command"


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


async def test_a_merge_fix_on_a_new_branch_opens_a_new_record(origin: Path, run_dir: Path):
    """The task's record holds the merged pull request; the merge-check fix publishes on a new branch, so
    the record becomes the new pull request, with its own body and provenance."""
    workspace, git_dir = run_dir / "workspace", run_dir / "git"
    await code_host.clone(str(origin), workspace, git_dir)
    (workspace / "hello.txt").write_text("changed\n")
    record = run_dir / "pull-request.md"
    common = dict(repo=str(origin), workspace=workspace, git_dir=git_dir, commit_message="m", pr_record=record)
    first = await code_host.publish(**common, branch="mh/task", title="t", body="first body")
    assert first is not None and first.created

    (workspace / "hello.txt").write_text("fixed after merge\n")
    fix = await code_host.publish(
        **common, branch="mh/task-fix", title="fix", body="fix body\n\n## Provenance\n\nrun: t-1:run-2"
    )

    assert fix is not None and fix.created
    text = record.read_text()
    assert f"head: mh/task-fix at {fix.commit}" in text
    assert "fix body" in text and "## Provenance" in text and "run: t-1:run-2" in text
    assert "first body" not in text

    again = await code_host.publish(**common, branch="mh/task-fix", title="fix", body="other body")
    assert again is not None and not again.created
    assert "fix body" in record.read_text() and "other body" not in record.read_text()


async def test_a_run_after_a_local_merge_on_the_same_branch_opens_a_new_record(origin: Path, run_dir: Path):
    """The record's pull request merged into main; a later task run on the same branch is a new pull request,
    with its own body and provenance."""
    workspace, git_dir = run_dir / "workspace", run_dir / "git"
    await code_host.clone(str(origin), workspace, git_dir)
    (workspace / "hello.txt").write_text("changed\n")
    record = run_dir / "pull-request.md"
    first = await code_host.publish(
        repo=str(origin), workspace=workspace, git_dir=git_dir, branch="mh/task", title="t", body="first body",
        commit_message="m", pr_record=record,
    )  # fmt: skip
    assert first is not None and first.created
    state, *_ = await code_host.merge_local(str(origin), "main", first.commit)
    assert state == "merged"

    later = run_dir.parent / "run-2"
    workspace, git_dir = later / "workspace", later / "git"
    await code_host.clone(str(origin), workspace, git_dir, branch="mh/task")
    (workspace / "hello.txt").write_text("changed again\n")
    second = await code_host.publish(
        repo=str(origin), workspace=workspace, git_dir=git_dir, branch="mh/task", title="t2",
        body="second body\n\n## Provenance\n\nrun: t-1:run-2", commit_message="m", pr_record=record,
    )  # fmt: skip

    assert second is not None and second.created
    text = record.read_text()
    assert f"head: mh/task at {second.commit}" in text
    assert "second body" in text and "run: t-1:run-2" in text
    assert "first body" not in text


async def test_a_description_written_by_a_function_is_given_the_files_the_branch_changes(origin: Path, run_dir: Path):
    workspace, git_dir = run_dir / "workspace", run_dir / "git"
    await code_host.clone(str(origin), workspace, git_dir)
    (workspace / "hello.txt").write_text("changed\n")
    given = []

    async def body(files: list[str]) -> str:
        given.append(files)
        return f"changes {', '.join(files)}"

    published = await code_host.publish(
        repo=str(origin),
        workspace=workspace,
        git_dir=git_dir,
        branch="mh/task",
        title="t",
        body=body,
        commit_message="m",
        pr_record=run_dir / "pull-request.md",
    )

    assert published is not None and given == [published.files] and "hello.txt" in published.files
    assert "changes hello.txt" in (run_dir / "pull-request.md").read_text()


async def test_a_runs_workspace_diff_counts_changed_and_new_files_against_its_clone(tmp_path, monkeypatch):
    import subprocess

    from meta_harness.run_service import code_host, sandbox

    monkeypatch.setattr(sandbox, "RUNS_DIR", tmp_path)
    workspace, git_dir = sandbox.workspace_for("t-1", "t-1:run-2"), sandbox.git_dir_for("t-1", "t-1:run-2")
    workspace.mkdir(parents=True)
    git = ["git", f"--git-dir={git_dir}", f"--work-tree={workspace}", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", f"--separate-git-dir={git_dir}", str(workspace)], check=True)
    (workspace / "app.py").write_text("a\nb\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    (workspace / "app.py").write_text("a\nB\nc\n")
    (workspace / "new.py").write_text("x\n")

    diff = await code_host.workspace_diff("t-1", "t-1:run-2")

    assert sorted((f["path"], f["additions"], f["deletions"]) for f in diff["files"]) == [
        ("app.py", 2, 1),
        ("new.py", 1, 0),
    ]
    assert "+B" in diff["patch"] and diff["missing"] is False
    assert (await code_host.workspace_diff("t-1", "t-1:run-9"))["missing"] is True


async def test_a_pull_request_s_description_is_read_from_github_or_from_a_local_record(monkeypatch, tmp_path):
    asked = []

    async def github(repo, permissions, method, path, **kwargs):
        asked.append((repo, method, path))
        return {"body": "## Summary from the run\r\n\r\nFixed it.\r\n"}

    monkeypatch.setattr(code_host, "_github", github)
    record = tmp_path / "pull-request.md"

    assert await code_host.description("o/r", "https://github.com/o/r/pull/7", record) == (
        "## Summary from the run\n\nFixed it.\n"
    )
    assert asked == [("o/r", "GET", "/repos/o/r/pulls/7")]
    assert await code_host.description(str(tmp_path), f"file://{record}", record) == ""
    record.write_text("# Fix\n\nbody\n", encoding="utf-8")
    assert await code_host.description(str(tmp_path), f"file://{record}", record) == "# Fix\n\nbody\n"


async def test_a_pull_request_is_closed_with_a_comment_and_its_branch_deleted_once(monkeypatch):
    from meta_harness import outbound

    said = await outbound.prepare(
        "Closed: started over.", outbound.Context(outbound.GITHUB, outbound.COMMENT, outbound.FIXED)
    )
    state = {"state": "open"}
    asked = []

    async def github(repo, permissions, method, path, **kwargs):
        asked.append((method, path))
        if method == "GET":
            return dict(state)
        if method == "DELETE" and state.get("gone"):
            raise code_host.CodeHostError("Reference does not exist", status=422)
        return {"html_url": "https://github.com/o/r/pull/7#c"}

    monkeypatch.setattr(code_host, "_github", github)

    did = await code_host.close_pull_request("o/r", 7, said, "mh/t-1")

    assert did == "closed the pull request, and deleted the branch mh/t-1"
    assert asked == [
        ("GET", "/repos/o/r/pulls/7"),
        ("POST", "/repos/o/r/issues/7/comments"),
        ("PATCH", "/repos/o/r/pulls/7"),
        ("DELETE", "/repos/o/r/git/refs/heads/mh/t-1"),
    ]
    # A repeat finds it closed and the branch gone: no second comment, and no error.
    state.update(state="closed", gone=True)
    asked.clear()
    assert "gone already" in await code_host.close_pull_request("o/r", 7, said, "mh/t-1")
    assert [m for m, _ in asked] == ["GET", "DELETE"]
    # A merged pull request is left alone.
    state.update(merged=True)
    asked.clear()
    assert "had merged" in await code_host.close_pull_request("o/r", 7, said, "mh/t-1")
    assert [m for m, _ in asked] == ["GET"]
