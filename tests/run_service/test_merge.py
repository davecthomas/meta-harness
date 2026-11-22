"""The merge's pieces, run for real: a conflict left for a fix run, a local merge, GitHub's answers, auto_merge."""

import asyncio
import subprocess
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

import meta_harness
from meta_harness import versioning
from meta_harness.run_service import activities, code_host, provenance
from meta_harness.run_service.code_host import CodeHostError
from meta_harness.run_service.provenance import COMMIT, Provenance
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


async def test_a_clean_merge_of_the_base_says_where_it_came_from(tmp_path: Path):
    """A conflict fix's merge that needs no resolving is a commit the meta-harness makes, with its trailers."""
    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "--quiet", "--initial-branch=main", cwd=seed)
    (seed / "app.py").write_text("x = 1\n")
    (seed / "other.py").write_text("y = 1\n")
    _git("add", ".", cwd=seed)
    _commit(seed, "seed")
    _git("checkout", "--quiet", "-b", "mh/t-1", cwd=seed)
    (seed / "app.py").write_text("x = 2\n")
    _commit(seed, "the task")
    _git("checkout", "--quiet", "main", cwd=seed)
    (seed / "other.py").write_text("y = 2\n")
    _commit(seed, "main moved on elsewhere")
    bare = tmp_path / "origin.git"
    _git("clone", "--quiet", "--bare", str(seed), str(bare), cwd=tmp_path)
    _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=bare)
    workspace, git_dir = tmp_path / "run" / "workspace", tmp_path / "run" / "git"
    signed = Provenance(COMMIT, task="t-1", run="run-task-t-1:run-2", version="1.2.3")
    message = provenance.commit_message("Merge origin/main", signed)

    await code_host.clone(str(bare), workspace, git_dir, branch="mh/t-1", merge_base="main", merge_message=message)

    assert not (git_dir / "MERGE_HEAD").is_file(), "the base merged in cleanly"
    logged = _git(f"--git-dir={git_dir}", "log", "-1", "--format=%B", cwd=tmp_path)
    assert logged.startswith("Merge origin/main")
    assert provenance.parse_trailers(logged) == {"version": "1.2.3", "task": "t-1", "run": "run-task-t-1:run-2"}


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


async def test_a_merge_commit_carries_its_provenance_as_trailers_after_its_version_line(monkeypatch):
    calls = _github(monkeypatch, None, {})
    signed = Provenance(COMMIT, task="t-1", version="1.2.3")

    await code_host.merge_pull_request("o/r", 7, "abc", kind="fix", provenance=signed)

    message = calls[0][2]["commit_message"]
    assert message == "Version: fix\nMeta-Harness-Version: 1.2.3\nMeta-Harness-Task: t-1"
    assert versioning.kind_of("Some task (#7)", message) == "fix", "the version line still counts"


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
    [(kind, payload)] = emitted
    assert kind == "pull_request_merged"
    assert {k: payload[k] for k in ("pr_url", "head_sha", "merge_sha")} == {
        "pr_url": "https://github.com/o/r/pull/7",
        "head_sha": "abc",
        "merge_sha": "m1",
    }
    assert payload["provenance"] == {"kind": "commit", "version": meta_harness.__version__, "task": "t-1"}


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


def _behind(monkeypatch, behind_by: int, update_status: int | None, heads: list[str], merged: dict | None = None):
    """A code host whose base is behind_by commits ahead of the branch; GETs of the pull request return heads
    in turn, and a merge of the base into the branch returns merged."""
    calls = []

    async def github(repo, permissions, method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        if "/compare/" in path:
            return {"behind_by": behind_by}
        if path.endswith("/merges"):
            if update_status:
                raise CodeHostError("GitHub answered 409: merge conflict", retryable=False, status=update_status)
            return {"sha": "with-base"} if merged is None else merged
        if method == "PUT":
            return {"sha": "m1", "merged": True}
        return {"head": {"sha": heads.pop(0) if len(heads) > 1 else heads[0], "ref": "mh/t-1"}}

    monkeypatch.setattr(code_host, "_github", github)
    return calls


async def test_a_branch_that_holds_the_base_merges_at_once(monkeypatch):
    calls = _behind(monkeypatch, 0, None, ["abc"])
    state, sha, head, _ = await code_host.merge_pull_request("o/r", 7, "abc", "main")
    assert (state, sha, head) == ("merged", "m1", "abc")
    assert calls[0] == ("GET", "/repos/o/r/compare/main...abc", None)


async def test_a_branch_behind_its_base_gets_the_base_and_goes_back_to_checks_and_review(monkeypatch):
    calls = _behind(monkeypatch, 2, None, ["abc"])
    state, sha, head, detail = await code_host.merge_pull_request("o/r", 7, "abc", "main")
    assert (state, sha, head) == ("head_changed", "", "with-base")
    assert "brought in 2 new commits from main" in detail
    merge_in = {"base": "mh/t-1", "head": "main", "commit_message": "Merge main into mh/t-1"}
    assert ("POST", "/repos/o/r/merges", merge_in) in calls
    assert not any(path.endswith("/merge") for _, path, _ in calls), "it does not merge before the new head is checked"


async def test_the_commit_that_brings_the_base_in_carries_its_provenance_as_trailers(monkeypatch):
    calls = _behind(monkeypatch, 2, None, ["abc"])
    signed = Provenance(COMMIT, task="t-1", version="1.2.3")

    await code_host.merge_pull_request("o/r", 7, "abc", "main", kind="fix", provenance=signed)

    [message] = [body["commit_message"] for _, path, body in calls if path.endswith("/merges")]
    assert message == "Merge main into mh/t-1\n\nMeta-Harness-Version: 1.2.3\nMeta-Harness-Task: t-1"
    assert not any(path.endswith("/update-branch") for _, path, _ in calls), "update-branch writes its own message"


async def test_a_head_that_moved_before_the_base_is_brought_in_is_a_new_head_and_nothing_is_merged(monkeypatch):
    calls = _behind(monkeypatch, 2, None, ["newer"])
    state, _, head, _ = await code_host.merge_pull_request("o/r", 7, "abc", "main")
    assert (state, head) == ("head_changed", "newer")
    assert not any(method != "GET" for method, _, _ in calls)


async def test_a_base_already_in_the_branch_when_merged_in_goes_on_to_the_merge(monkeypatch):
    _behind(monkeypatch, 1, None, ["abc"], merged={})
    state, sha, head, _ = await code_host.merge_pull_request("o/r", 7, "abc", "main")
    assert (state, sha, head) == ("merged", "m1", "abc")


async def test_a_base_that_does_not_merge_in_cleanly_is_a_conflict_for_a_fix_run(monkeypatch):
    _behind(monkeypatch, 1, 409, ["abc"])
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


async def test_merge_pr_holds_a_change_to_ci_or_deploy_files_for_an_operator(monkeypatch):
    """A workflow runs with the repo's secrets and a deploy applies as the project's owner, so a pull request
    that changes one does not merge on a model's review alone; an operator's own merge goes through."""
    merges = []

    async def merge_pull_request(repo, number, head_sha, base, kind="", provenance=None):
        merges.append(number)
        return "merged", "m1", head_sha, ""

    async def pull_request_files(repo, number):
        return [
            {"path": "src/app.py", "previous_path": None},
            {"path": "docs/moved.sh", "previous_path": "deploy/vm/boot.sh"},
            {"path": ".github/workflows/deploy.yml", "previous_path": None},
        ]

    monkeypatch.setattr(code_host, "merge_pull_request", merge_pull_request)
    monkeypatch.setattr(code_host, "pull_request_files", pull_request_files)
    monkeypatch.setattr(activities.events, "emit", lambda *a, **k: None)
    request = MergeRequest(
        task_id="t-1", repo="o/r", pr_url="https://github.com/o/r/pull/7", branch="mh/t-1", base="main", head_sha="abc"
    )

    held = await ActivityEnvironment().run(activities.merge_pr, request)

    assert held.state == "blocked" and merges == []
    assert held.detail.startswith("held for an operator: it changes .github/workflows/deploy.yml, deploy/vm/boot.sh")
    assert "use Merge on the task's page" in held.detail

    # The supervisor's merge of an auto-merge goal's task is asked for, and is still not an operator's.
    request.asked_for = True
    assert (await ActivityEnvironment().run(activities.merge_pr, request)).state == "blocked" and merges == []

    request.by_operator = True
    assert (await ActivityEnvironment().run(activities.merge_pr, request)).state == "merged" and merges == [7]


async def test_merge_pr_says_when_the_base_came_in_without_changing_the_diff(monkeypatch):
    async def merge_pull_request(repo, number, head_sha, base, kind="", provenance=None):
        return "head_changed", "", "new", "brought in 2 new commits from main"

    async def same_diff(repo, base, before, after):
        return (base, before, after) == ("main", "abc", "new")

    async def pull_request_files(repo, number):
        return [{"path": "src/app.py", "previous_path": None}]

    monkeypatch.setattr(code_host, "merge_pull_request", merge_pull_request)
    monkeypatch.setattr(code_host, "pull_request_files", pull_request_files)
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


async def test_the_bases_health_is_its_newest_commits_checks(monkeypatch):
    from meta_harness.run_service.code_host import CheckRun
    from meta_harness.run_service.shared import BaseHealthRequest

    runs: list = []

    async def branch_head(repo, branch):
        return "abc123"

    async def check_runs(repo, sha):
        return runs

    monkeypatch.setattr(code_host, "branch_head", branch_head)
    monkeypatch.setattr(code_host, "check_runs", check_runs)

    async def health():
        result = await ActivityEnvironment().run(activities.base_health, BaseHealthRequest(repo="o/r", base="main"))
        return result.state, result.sha, result.checks

    assert await health() == ("healthy", "abc123", []), "a base that runs no checks is healthy"
    runs[:] = [CheckRun("tests", "completed", "success", ""), CheckRun("deploy", "in_progress", None, "")]
    assert await health() == ("running", "abc123", ["deploy"])
    runs[:] = [CheckRun("tests", "completed", "failure", "x"), CheckRun("deploy", "in_progress", None, "")]
    assert await health() == ("failed", "abc123", ["tests"]), "a failure counts before the rest finish"
    # A deploy a later merge replaced is canceled: no sign the base is broken.
    runs[:] = [CheckRun("tests", "completed", "success", ""), CheckRun("deploy", "completed", "cancelled", "")]
    assert (await health())[0] == "healthy"
    local = await ActivityEnvironment().run(
        activities.base_health, BaseHealthRequest(repo="/tmp/origin.git", base="main")
    )
    assert local.state == "healthy"
