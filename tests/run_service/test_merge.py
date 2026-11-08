"""The merge's pieces, run for real: a conflict left for a fix run, a local merge, GitHub's answers, auto_merge."""

import subprocess
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from meta_harness.run_service import activities, code_host
from meta_harness.run_service.code_host import CodeHostError
from meta_harness.run_service.shared import MergeRequest


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _commit(cwd: Path, message: str) -> None:
    _git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "--quiet", "-am", message, cwd=cwd)


@pytest.fixture
def diverged(tmp_path: Path) -> Path:
    """A bare repo whose branch mh/t-1 and main both changed line 1 of app.py since they split."""
    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "--quiet", "--initial-branch=main", cwd=seed)
    (seed / "app.py").write_text("x = 1\n")
    _git("add", ".", cwd=seed)
    _commit(seed, "seed")
    _git("checkout", "--quiet", "-b", "mh/t-1", cwd=seed)
    (seed / "app.py").write_text("x = 2\n")
    _commit(seed, "the task")
    _git("checkout", "--quiet", "main", cwd=seed)
    (seed / "app.py").write_text("x = 3\n")
    _commit(seed, "main moved on")
    bare = tmp_path / "origin.git"
    _git("clone", "--quiet", "--bare", str(seed), str(bare), cwd=tmp_path)
    _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=bare)
    return bare


async def test_a_conflict_fix_run_gets_the_base_merged_in_with_its_markers(diverged: Path, tmp_path: Path):
    workspace, git_dir = tmp_path / "run" / "workspace", tmp_path / "run" / "git"

    await code_host.clone(str(diverged), workspace, git_dir, branch="mh/t-1", merge_base="main")

    text = (workspace / "app.py").read_text()
    assert "<<<<<<<" in text and "x = 2" in text and "x = 3" in text
    assert (git_dir / "MERGE_HEAD").is_file(), "the publish's commit completes the merge"


async def test_a_local_merge_fast_forwards_the_base_or_reports_a_conflict(diverged: Path):
    branch_head = _git("rev-parse", "mh/t-1", cwd=diverged)
    state, _, _, detail = await code_host.merge_local(str(diverged), "main", branch_head)
    assert state == "conflict" and "moved on" in detail

    main_head = _git("rev-parse", "main", cwd=diverged)
    _git("update-ref", "refs/heads/mh/t-2", main_head, cwd=diverged)
    state, sha, _, _ = await code_host.merge_local(str(diverged), "main", main_head)
    assert state == "merged" and sha == main_head


def _github(monkeypatch, merge_status: int | None, pull: dict):
    calls = []

    async def github(repo, permissions, method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        if method == "PUT":
            if merge_status is None:
                return {"sha": "m1", "merged": True}
            raise CodeHostError(f"GitHub answered {merge_status}", retryable=False, status=merge_status)
        return pull

    monkeypatch.setattr(code_host, "_github", github)
    return calls


async def test_a_merge_names_the_reviewed_commit_and_squashes(monkeypatch):
    calls = _github(monkeypatch, None, {})

    state, sha, head, _ = await code_host.merge_pull_request("o/r", 7, "abc")

    assert (state, sha, head) == ("merged", "m1", "abc")
    assert calls[0] == ("PUT", "/repos/o/r/pulls/7/merge", {"sha": "abc", "merge_method": "squash"})


@pytest.mark.parametrize(
    ("status", "pull", "expected"),
    [
        (409, {"head": {"sha": "new"}, "mergeable_state": "clean"}, ("head_changed", "new")),
        (405, {"head": {"sha": "abc"}, "mergeable_state": "dirty", "mergeable": False}, ("conflict", "abc")),
        (405, {"head": {"sha": "abc"}, "mergeable_state": "blocked", "mergeable": True}, ("blocked", "abc")),
    ],
)
async def test_githubs_refusals_become_the_loops_next_step(monkeypatch, status, pull, expected):
    _github(monkeypatch, status, pull)

    state, _, head, _ = await code_host.merge_pull_request("o/r", 7, "abc")

    assert (state, head) == expected


async def test_any_other_merge_error_is_raised(monkeypatch):
    _github(monkeypatch, 500, {})
    with pytest.raises(CodeHostError):
        await code_host.merge_pull_request("o/r", 7, "abc")


async def test_a_merge_records_the_merged_event(monkeypatch):
    _github(monkeypatch, None, {})
    emitted = []
    monkeypatch.setattr(activities.events, "emit", lambda kind, payload, **f: emitted.append((kind, payload)))
    request = MergeRequest(
        task_id="t-1",
        repo="o/r",
        pr_url="https://github.com/o/r/pull/7",
        branch="mh/t-1",
        base="main",
        head_sha="abc",
    )

    result = await ActivityEnvironment().run(activities.merge_pr, request)

    assert result.state == "merged" and result.merge_sha == "m1"
    assert emitted == [
        ("pull_request_merged", {"pr_url": "https://github.com/o/r/pull/7", "head_sha": "abc", "merge_sha": "m1"})
    ]


async def test_auto_merge_is_read_from_the_goal_as_the_planner_has_it(monkeypatch):
    from meta_harness.planner import client

    class Goals:
        def __init__(self, api_key=None, **kwargs):
            assert api_key == "svc"

        def get(self, goal_id):
            return {"id": goal_id, "auto_merge": goal_id == "g-on"}

    monkeypatch.setenv("MH_SERVICE_KEY", "svc")
    monkeypatch.setattr(client, "GoalsClient", Goals)

    assert await ActivityEnvironment().run(activities.goal_auto_merge, "g-on") is True
    assert await ActivityEnvironment().run(activities.goal_auto_merge, "g-off") is False
