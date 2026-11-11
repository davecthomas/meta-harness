"""The review loop's activities: reviewers, prompts, checks, the posted review, and a clone of the PR's branch.

The workflow tests stand these in; here each one runs for real, against a
local bare repo for git and a fake code host for GitHub's API.
"""

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from meta_harness.run_service import activities, code_host, routing
from meta_harness.run_service.code_host import CheckRun, CodeHostError
from meta_harness.run_service.shared import ChecksRequest, FailedCheck, LoopPromptRequest, PostReviewRequest


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


# --- Reviewers ----------------------------------------------------------------------------------------


def _policy(tmp_path: Path, monkeypatch, reviewers: list[str], max_rounds=3) -> None:
    path = tmp_path / "task_policy.json"
    path.write_text(json.dumps({"max_parallel": 2, "review": {"reviewers": reviewers, "max_rounds": max_rounds}}))
    monkeypatch.setattr(routing, "TASK_POLICY_PATH", path)


async def test_the_model_that_wrote_the_pull_request_never_reviews_it(tmp_path, monkeypatch):
    _policy(
        tmp_path,
        monkeypatch,
        ["harness:pi-review/claude:claude-opus-5-5", "harness:pi-review/openai-responses:gpt-6-sol"],
    )

    policy = await ActivityEnvironment().run(activities.review_policy, "claude:claude-opus-5-5")

    assert [r.label for r in policy.reviewers] == ["harness:pi-review/openai-responses:gpt-6-sol"]
    assert policy.max_rounds == 3 and policy.unresolved == []


async def test_a_reviewer_pair_with_no_active_profile_is_skipped_and_named(tmp_path, monkeypatch):
    _policy(
        tmp_path,
        monkeypatch,
        ["harness:pi-gone/claude:claude-opus-5-5", "harness:pi-review/openai-responses:gpt-6-sol"],
    )

    policy = await ActivityEnvironment().run(activities.review_policy, "claude:claude-sonnet-5")

    assert [r.label for r in policy.reviewers] == ["harness:pi-review/openai-responses:gpt-6-sol"]
    assert len(policy.unresolved) == 1 and "pi-gone" in policy.unresolved[0]


def test_a_malformed_task_policy_stops_the_worker_and_a_missing_profile_only_warns(tmp_path, monkeypatch):
    _policy(tmp_path, monkeypatch, [], max_rounds=3)
    with pytest.raises(routing.CandidateError, match="non-empty list"):
        routing.review_policy_lines()
    _policy(tmp_path, monkeypatch, ["harness:pi-review/claude:x"], max_rounds="3")
    with pytest.raises(routing.CandidateError, match="whole number"):
        routing.review_policy_lines()
    _policy(tmp_path, monkeypatch, ["harness:pi-gone/claude:x"], max_rounds=2)
    lines = routing.review_policy_lines()
    assert lines[:2] == ["tasks: up to 2 at once", "review: up to 2 fix rounds"]
    assert "cannot run, so the review loop skips it" in lines[2]


# --- Prompts ------------------------------------------------------------------------------------------


def _prompt_request(**fields) -> LoopPromptRequest:
    return LoopPromptRequest(
        repo="owner/repo",
        title="Fix the bug",
        description="The bug is in app.py.",
        checks=["pytest -q"],
        base="main",
        branch="mh/t-1",
        **fields,
    )


async def test_the_review_prompt_names_the_branch_the_base_and_the_checks():
    prompt = await ActivityEnvironment().run(activities.loop_prompt, _prompt_request(kind="review"))

    assert "branch mh/t-1" in prompt and "git diff origin/main...HEAD" in prompt
    assert "Fix the bug" in prompt and "The bug is in app.py." in prompt and "- pytest -q" in prompt
    assert "submit_review" in prompt
    assert "$" not in prompt, "every placeholder is filled"


async def test_each_prompt_names_the_issues_paths_and_the_review_flags_a_change_outside_them():
    named = "The bug is in app.py.\n\nPaths: src/app, docs\n"
    review = await ActivityEnvironment().run(
        activities.loop_prompt, dataclasses.replace(_prompt_request(kind="review"), description=named)
    )
    fix = await ActivityEnvironment().run(
        activities.loop_prompt, dataclasses.replace(_prompt_request(kind="fix"), description=named)
    )
    assert "The task names its paths: src/app, docs. A changed file outside them" in review
    assert "Keep to the task's paths: src/app, docs." in fix
    unnamed = await ActivityEnvironment().run(activities.loop_prompt, _prompt_request(kind="review"))
    assert "The task names no paths" in unnamed and "$" not in unnamed


async def test_every_prompt_keeps_the_version_files_in_scope_whatever_the_paths():
    named = "The bug is in app.py.\n\nPaths: src/app\n"
    for description in (named, "The bug is in app.py."):
        review = await ActivityEnvironment().run(
            activities.loop_prompt, dataclasses.replace(_prompt_request(kind="review"), description=description)
        )
        fix = await ActivityEnvironment().run(
            activities.loop_prompt, dataclasses.replace(_prompt_request(kind="fix"), description=description)
        )
        assert "a version bump by that rule is never a finding" in review
        assert "never revert a bump it requires" in fix


async def test_a_first_review_reads_the_whole_change_and_a_later_one_judges_the_round_befores_findings_first():
    first = await ActivityEnvironment().run(activities.loop_prompt, _prompt_request(kind="review", round_no=1))
    assert "This is the first review of this pull request" in first

    later = await ActivityEnvironment().run(
        activities.loop_prompt,
        _prompt_request(
            kind="review",
            round_no=2,
            previous_summary="One bug.",
            previous_findings=[{"path": "app.py", "line": 3, "body": "Off by one."}],
        ),
    )
    assert "This is review round 2." in later and "Its summary: One bug." in later
    assert "1. app.py line 3: Off by one." in later
    assert "Judge first whether each of those is fixed" in later
    assert "Raise a new finding only for a bug, a failing check, or a part of the task left undone" in later
    assert "$" not in later, "every placeholder is filled"


async def test_a_conflict_fix_prompt_resolves_a_version_conflict_by_the_agents_rule():
    prompt = await ActivityEnvironment().run(activities.loop_prompt, _prompt_request(kind="fix", conflict=True))
    assert "conflict markers" in prompt
    assert "take the base's version and apply this pull request's bump on top of it" in prompt
    assert "Never keep the branch's older number" in prompt and "uv lock" in prompt


async def test_a_fix_prompt_lists_each_finding_with_its_place():
    findings = [
        {"path": "app.py", "line": 3, "body": "Off by one."},
        {"path": None, "line": None, "body": "Add a test."},
    ]
    request = _prompt_request(kind="fix", round_no=2, findings=findings, summary="Two things.")

    prompt = await ActivityEnvironment().run(activities.loop_prompt, request)

    assert "Round 2" in prompt
    assert "1. app.py line 3: Off by one." in prompt
    assert "2. Add a test." in prompt
    assert "The review's summary: Two things." in prompt
    assert "$" not in prompt


async def test_a_fix_prompt_for_failed_checks_carries_their_output():
    request = _prompt_request(kind="fix", round_no=1, failed_checks=[FailedCheck("tests", "1 failed: test_app")])

    prompt = await ActivityEnvironment().run(activities.loop_prompt, request)

    assert "These checks failed" in prompt and "Check tests:\n1 failed: test_app" in prompt


# --- Checks -------------------------------------------------------------------------------------------


def _checks_seq(monkeypatch, answers: list[list[CheckRun]]):
    calls = []

    async def check_runs(repo, sha):
        calls.append(sha)
        return answers[min(len(calls) - 1, len(answers) - 1)]

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(code_host, "check_runs", check_runs)
    monkeypatch.setattr(activities.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(activities.events, "emit", lambda *a, **k: None)
    return calls


async def test_checks_are_waited_on_until_every_one_completes(monkeypatch):
    pending = [CheckRun("tests", "in_progress", None, ""), CheckRun("lint", "completed", "success", "")]
    done = [CheckRun("tests", "completed", "success", ""), CheckRun("lint", "completed", "skipped", "")]
    calls = _checks_seq(monkeypatch, [pending, pending, done])

    result = await ActivityEnvironment().run(activities.wait_checks, ChecksRequest(repo="owner/repo", head_sha="abc"))

    assert result.state == "passed" and len(calls) == 3


async def test_a_failed_check_is_reported_with_its_summary(monkeypatch):
    runs = [CheckRun("tests", "completed", "failure", "1 failed"), CheckRun("lint", "completed", "success", "")]
    _checks_seq(monkeypatch, [runs])

    result = await ActivityEnvironment().run(activities.wait_checks, ChecksRequest(repo="owner/repo", head_sha="abc"))

    assert result.state == "failed"
    assert [(f.name, f.summary) for f in result.failed] == [("tests", "1 failed")]


async def test_a_repo_with_no_checks_counts_as_none_after_the_wait(monkeypatch):
    calls = _checks_seq(monkeypatch, [[]])

    result = await ActivityEnvironment().run(activities.wait_checks, ChecksRequest(repo="owner/repo", head_sha="abc"))

    assert result.state == "none"
    assert len(calls) == activities.NO_CHECKS_SECONDS // activities.CHECKS_POLL_SECONDS + 1


# --- The posted review --------------------------------------------------------------------------------


def _review_request(review: dict) -> PostReviewRequest:
    return PostReviewRequest(
        task_id="",
        repo="owner/repo",
        pr_url="https://github.com/owner/repo/pull/7",
        head_sha="abc",
        reviewer="harness:pi-review/openai:gpt-6-sol",
        review=review,
        round_no=1,
    )


async def test_a_review_posts_line_findings_inline_and_the_rest_in_its_body(monkeypatch):
    posted = []

    async def github(repo, permissions, method, path, **kwargs):
        posted.append((method, path, kwargs["json"]))
        return {"html_url": "https://github.com/owner/repo/pull/7#pullrequestreview-1"}

    monkeypatch.setattr(code_host, "_github", github)
    review = {
        "verdict": "changes",
        "summary": "Two things.",
        "findings": [
            {"path": "app.py", "line": 3, "body": "Off by one."},
            {"path": None, "line": None, "body": "Docs."},
        ],
    }

    url = await ActivityEnvironment().run(activities.post_review, _review_request(review))

    assert url.endswith("pullrequestreview-1")
    method, path, body = posted[0]
    assert (method, path) == ("POST", "/repos/owner/repo/pulls/7/reviews")
    assert body["event"] == "COMMENT" and body["commit_id"] == "abc"
    assert body["comments"] == [{"path": "app.py", "line": 3, "side": "RIGHT", "body": "Off by one."}]
    assert "`changes`" in body["body"] and "Two things." in body["body"] and "- Docs." in body["body"]


async def test_a_finding_github_cannot_place_moves_into_the_body(monkeypatch):
    posted = []

    async def github(repo, permissions, method, path, **kwargs):
        posted.append(kwargs["json"])
        if kwargs["json"]["comments"]:
            raise CodeHostError("GitHub answered 422: line must be part of the diff", retryable=False, status=422)
        return {"html_url": "https://github.com/owner/repo/pull/7#pullrequestreview-2"}

    monkeypatch.setattr(code_host, "_github", github)
    review = {"verdict": "changes", "summary": "One.", "findings": [{"path": "old.py", "line": 90, "body": "Wrong."}]}

    await ActivityEnvironment().run(activities.post_review, _review_request(review))

    assert len(posted) == 2 and posted[1]["comments"] == []
    assert "`old.py` line 90: Wrong." in posted[1]["body"]


# --- A clone of the pull request's branch -------------------------------------------------------------


async def test_a_review_or_fix_run_clones_the_pull_requests_branch(tmp_path: Path):
    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "--quiet", "--initial-branch=main", cwd=seed)
    (seed / "app.py").write_text("x = 1\n")
    _git("add", ".", cwd=seed)
    _git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "--quiet", "-m", "seed", cwd=seed)
    _git("checkout", "--quiet", "-b", "mh/t-1", cwd=seed)
    (seed / "app.py").write_text("x = 2\n")
    _git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "--quiet", "-am", "the fix", cwd=seed)
    bare = tmp_path / "origin.git"
    _git("clone", "--quiet", "--bare", str(seed), str(bare), cwd=tmp_path)
    _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=bare)

    workspace, git_dir = tmp_path / "run" / "workspace", tmp_path / "run" / "git"
    branch = await code_host.clone(str(bare), workspace, git_dir, branch="mh/t-1")

    assert branch == "mh/t-1"
    assert (workspace / "app.py").read_text() == "x = 2\n", "the branch's work is checked out"


async def test_a_local_repo_runs_no_checks_and_its_result_is_still_recorded(monkeypatch):
    emitted = []
    monkeypatch.setattr(activities.events, "emit", lambda kind, payload, **fields: emitted.append((kind, payload)))

    request = ChecksRequest(repo="/tmp/origin.git", head_sha="abc", task_id="t-1")
    result = await ActivityEnvironment().run(activities.wait_checks, request)

    assert result.state == "none"
    assert emitted == [("pull_request_checks", {"head_sha": "abc", "state": "none", "failed": []})]


async def test_a_reviewer_on_the_openai_engine_runs_on_openai_responses(tmp_path, monkeypatch):
    _policy(tmp_path, monkeypatch, ["harness:pi-review/openai:gpt-6-sol"])

    policy = await ActivityEnvironment().run(activities.review_policy, "claude:claude-opus-5-5")

    assert [r.label for r in policy.reviewers] == ["harness:pi-review/openai-responses:gpt-6-sol"]


async def test_the_author_is_matched_by_model_whatever_engine_reached_it(tmp_path, monkeypatch):
    _policy(tmp_path, monkeypatch, ["harness:pi-review/openai-responses:gpt-6-sol"])

    policy = await ActivityEnvironment().run(activities.review_policy, "openai:gpt-6-sol")

    assert policy.reviewers == []


def test_no_shipped_candidate_or_reviewer_names_an_engine_a_harness_cannot_use():
    from meta_harness.run_service.paths import REPO_ROOT

    router = json.loads((REPO_ROOT / "config" / "models.json").read_text())["router"]
    pairs = [m for tier in router["tiers"] for m in tier["models"]]
    pairs += json.loads((REPO_ROOT / "config" / "task_policy.json").read_text())["review"]["reviewers"]
    assert pairs and all(routing.replace_engine(p) == p for p in pairs), pairs


async def test_a_review_is_told_which_checks_passed_and_not_to_run_them_again():
    told = await ActivityEnvironment().run(
        activities.loop_prompt, _prompt_request(kind="review", passed_checks=["control plane tests", "console web app"])
    )
    assert (
        "These checks passed on the code host at the commit you are reviewing: control plane tests, console web app."
        in told
    )
    assert "Do not run them again" in told and "Run each acceptance check with the bash tool" not in told
    assert "- pytest -q" in told, "the task's acceptance checks are still named"

    alone = await ActivityEnvironment().run(activities.loop_prompt, _prompt_request(kind="review"))
    assert "Run each acceptance check with the bash tool." in alone and "$" not in alone


async def test_wait_checks_names_the_checks_that_passed(monkeypatch):
    from meta_harness.run_service.code_host import CheckRun
    from meta_harness.run_service.shared import ChecksRequest

    async def check_runs(repo, sha):
        return [
            CheckRun("tests", "completed", "success", ""),
            CheckRun("lint", "completed", "skipped", ""),
            CheckRun("deploy", "completed", "failure", "x"),
        ]

    monkeypatch.setattr(activities.code_host, "check_runs", check_runs)
    monkeypatch.setattr(activities.events, "emit", lambda *a, **k: None)
    result = await ActivityEnvironment().run(
        activities.wait_checks, ChecksRequest(repo="o/r", head_sha="abc", task_id="t-1")
    )
    assert result.state == "failed" and [f.name for f in result.failed] == ["deploy"]
    assert "tests" in result.passed and "deploy" not in result.passed
