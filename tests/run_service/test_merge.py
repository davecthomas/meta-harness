"""The merge's pieces, run for real: a conflict left for a fix run, a local merge, GitHub's answers, auto_merge."""

import asyncio
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


async def test_a_merge_says_what_kind_of_change_it_is_in_the_merge_commit(monkeypatch):
    calls = _github(monkeypatch, None, {})

    await code_host.merge_pull_request("o/r", 7, "abc", kind="feature")

    assert calls[0][2] == {"sha": "abc", "merge_method": "squash", "commit_message": "Version: feature"}


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


def _behind(monkeypatch, behind_by: int, update_status: int | None, heads: list[str]):
    """A code host whose base is behind_by commits ahead of the branch; GETs of the pull request return heads
    in turn."""
    calls = []
    monkeypatch.setattr(code_host, "UPDATE_BRANCH_POLL_SECONDS", 0)

    async def github(repo, permissions, method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        if "/compare/" in path:
            return {"behind_by": behind_by}
        if path.endswith("/update-branch"):
            if update_status:
                raise CodeHostError("GitHub answered 422: merge conflict", retryable=False, status=update_status)
            return {"message": "Updating pull request branch."}
        if method == "PUT":
            return {"sha": "m1", "merged": True}
        return {"head": {"sha": heads.pop(0) if len(heads) > 1 else heads[0]}}

    monkeypatch.setattr(code_host, "_github", github)
    return calls


async def test_a_branch_that_holds_the_base_merges_at_once(monkeypatch):
    calls = _behind(monkeypatch, 0, None, ["abc"])
    state, sha, head, _ = await code_host.merge_pull_request("o/r", 7, "abc", "main")
    assert (state, sha, head) == ("merged", "m1", "abc")
    assert calls[0] == ("GET", "/repos/o/r/compare/main...abc", None)


async def test_a_branch_behind_its_base_gets_the_base_and_goes_back_to_checks_and_review(monkeypatch):
    calls = _behind(monkeypatch, 2, None, ["abc", "abc", "with-base"])
    state, sha, head, detail = await code_host.merge_pull_request("o/r", 7, "abc", "main")
    assert (state, sha, head) == ("head_changed", "", "with-base")
    assert "brought in 2 new commits from main" in detail
    assert ("PUT", "/repos/o/r/pulls/7/update-branch", {"expected_head_sha": "abc"}) in calls
    assert not any(path.endswith("/merge") for _, path, _ in calls), "it does not merge before the new head is checked"


async def test_a_base_that_does_not_merge_in_cleanly_is_a_conflict_for_a_fix_run(monkeypatch):
    _behind(monkeypatch, 1, 422, ["abc"])
    state, _, head, _ = await code_host.merge_pull_request("o/r", 7, "abc", "main")
    assert (state, head) == ("conflict", "abc")


async def test_one_merge_at_a_time_per_repo():
    order = []

    async def merge(name: str, repo: str, hold: float) -> None:
        async with activities.merge_lock(repo):
            order.append(f"{name} in")
            await asyncio.sleep(hold)
            order.append(f"{name} out")

    first = asyncio.create_task(merge("a", "o/r", 1.5))
    while "a in" not in order:
        await asyncio.sleep(0.01)
    await asyncio.gather(first, merge("b", "o/r", 0), merge("c", "o/other", 0))
    assert order.index("a out") < order.index("b in"), order
    assert order.index("c in") < order.index("a out"), "another repo's merge does not wait"


async def test_a_check_a_later_merge_canceled_is_judged_by_the_same_check_on_the_newer_base(monkeypatch):
    from meta_harness.run_service.code_host import CheckRun
    from meta_harness.run_service.shared import MergeChecksRequest

    runs = {
        "m1": [CheckRun("tests", "completed", "success", ""), CheckRun("deploy", "completed", "cancelled", "")],
        "m2": [CheckRun("deploy", "completed", "success", "")],
    }

    async def check_runs(repo, sha):
        return runs[sha]

    async def newest_containing(repo, base, sha):
        return "m2"

    monkeypatch.setattr(code_host, "check_runs", check_runs)
    monkeypatch.setattr(code_host, "newest_containing", newest_containing)
    monkeypatch.setattr(activities.events, "emit", lambda *a, **k: None)
    request = MergeChecksRequest(repo="o/r", merge_sha="m1", base="main", task_id="t-1")

    result = await ActivityEnvironment().run(activities.wait_merge_checks, request)
    assert result.state == "passed"

    runs["m2"] = [CheckRun("deploy", "completed", "failure", "apply failed")]
    result = await ActivityEnvironment().run(activities.wait_merge_checks, request)
    assert result.state == "failed" and [f.name for f in result.failed] == ["deploy"]


def _compares(monkeypatch, diffs: dict[str, list[dict] | None]):
    """GitHub's compare of the base and each head: the files the pull request changes there."""

    async def github(repo, permissions, method, path, **kwargs):
        head = path.rsplit("...", 1)[-1]
        return {} if diffs[head] is None else {"files": diffs[head]}

    monkeypatch.setattr(code_host, "_github", github)


APP = {"filename": "app.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"}


async def test_a_head_that_only_gained_the_base_has_the_same_diff(monkeypatch):
    _compares(monkeypatch, {"old": [APP], "new": [dict(APP)]})
    assert await code_host.same_diff("o/r", "main", "old", "new")


@pytest.mark.parametrize(
    "new",
    [
        [{**APP, "patch": "@@ -1 +1 @@\n-a\n+c"}],  # a commit changed the pull request's own lines
        [APP, {"filename": "new.py", "status": "added", "patch": "@@ -0,0 +1 @@\n+x"}],  # or added a file
        [{**APP, "patch": "@@ -3 +3 @@\n-a\n+b"}],  # or the base moved the lines around its change
    ],
)
async def test_a_head_whose_own_change_differs_is_not_the_same_diff(monkeypatch, new):
    _compares(monkeypatch, {"old": [APP], "new": new})
    assert not await code_host.same_diff("o/r", "main", "old", "new")


@pytest.mark.parametrize(
    "old",
    [None, [{"filename": "logo.png", "status": "modified"}], [APP] * code_host.COMPARE_FILES],
)
async def test_a_diff_that_cannot_be_read_whole_is_never_called_the_same(monkeypatch, old):
    _compares(monkeypatch, {"old": old, "new": old})
    assert not await code_host.same_diff("o/r", "main", "old", "new")


async def test_merge_pr_says_when_the_base_came_in_without_changing_the_diff(monkeypatch):
    async def merge_pull_request(repo, number, head_sha, base, kind=""):
        return "head_changed", "", "new", "brought in 2 new commits from main"

    async def same_diff(repo, base, before, after):
        return (base, before, after) == ("main", "abc", "new")

    monkeypatch.setattr(code_host, "merge_pull_request", merge_pull_request)
    monkeypatch.setattr(code_host, "same_diff", same_diff)
    request = MergeRequest(
        task_id="t-1", repo="o/r", pr_url="https://github.com/o/r/pull/7", branch="mh/t-1", base="main", head_sha="abc"
    )

    result = await ActivityEnvironment().run(activities.merge_pr, request)

    assert result.state == "head_changed" and result.head_sha == "new" and result.same_diff


async def _judged(monkeypatch, merge: list, newer: list | None):
    """The merge commit m1's checks as _merge_runs judges them, with the base's newest commit m2 (None: no newer)."""
    from meta_harness.run_service.shared import MergeChecksRequest

    async def check_runs(repo, sha):
        return merge if sha == "m1" else newer

    async def newest_containing(repo, base, sha):
        return "m1" if newer is None else "m2"

    monkeypatch.setattr(code_host, "check_runs", check_runs)
    monkeypatch.setattr(code_host, "newest_containing", newest_containing)
    request = MergeChecksRequest(repo="o/r", merge_sha="m1", base="main", task_id="t-1")
    return [(r.name, r.status, r.conclusion) for r in await activities._merge_runs(request)]


async def test_a_failed_merge_check_is_judged_by_the_same_check_on_a_newer_commit(monkeypatch):
    from meta_harness.run_service.code_host import CheckRun

    failed = [CheckRun("tests", "completed", "success", ""), CheckRun("deploy", "completed", "failure", "")]
    # No newer commit: the failure stands.
    assert await _judged(monkeypatch, failed, None) == [
        ("tests", "completed", "success"),
        ("deploy", "completed", "failure"),
    ]
    # The newer commit's deploy passed: so did this merge's.
    newer = [CheckRun("deploy", "completed", "success", "")]
    assert ("deploy", "completed", "success") in await _judged(monkeypatch, failed, newer)
    # It failed there too: it failed.
    newer = [CheckRun("deploy", "completed", "failure", "apply failed")]
    assert ("deploy", "completed", "failure") in await _judged(monkeypatch, failed, newer)
    # The newer commit's checks are still starting: wait for its deploy.
    assert ("deploy", "queued", None) in await _judged(monkeypatch, failed, [])
    assert ("deploy", "queued", None) in await _judged(
        monkeypatch, failed, [CheckRun("tests", "in_progress", None, "")]
    )
    # They finished and never ran a deploy: the failure stands.
    assert ("deploy", "completed", "failure") in await _judged(
        monkeypatch, failed, [CheckRun("tests", "completed", "success", "")]
    )
