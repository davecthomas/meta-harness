"""A repo's standing checks are run on its base once for a commit, and each run and review is told what
fails there (run_service/base_checks.py)."""

import uuid

import pytest
from temporalio.testing import ActivityEnvironment

from meta_harness.planner import repos
from meta_harness.run_service import activities, base_checks, code_host, events, sandbox
from meta_harness.run_service.base_checks import BaseChecksRequest, FailedBaseCheck
from meta_harness.run_service.shared import LoopPromptRequest

from .test_review_loop import CLEAN, REPO, Script, _run, env  # noqa: F401 - env is a fixture

CHECKS = ["uv sync", "uv run pytest -q"]


@pytest.fixture
def base(conn, monkeypatch):
    """A repo with two standing checks, whose second fails in the sandbox, and a record of what was run."""
    from meta_harness import migrations

    migrations.apply(conn)
    repo = f"owner/repo-{uuid.uuid4().hex[:8]}"
    ran: list[str] = []
    emitted: list[tuple[str, dict, dict]] = []
    released: list[str] = []

    async def base_head(name):
        return "b0b0b0"

    async def clone_at(tag, name, commit, ref, run_id):
        return None

    async def lease(tag, run_id, ref, image, resources, services=()):
        return None

    async def release(tag, run_id, ref):
        released.append(run_id)

    async def run_in(run_id, command, seconds):
        ran.append(command)
        if "pytest" in command:
            return 1, "x" * 5000 + "\nFAILED tests/test_app.py::test_adds - assert 3 == 4\n1 failed, 9 passed"
        return 0, "ok"

    monkeypatch.setattr(repos, "get", lambda name: repos.Repo(name, "github", CHECKS) if name == repo else None)

    async def no_host_checks(name, commit):
        return []

    monkeypatch.setattr(code_host, "check_runs", no_host_checks)
    monkeypatch.setattr(code_host, "base_head", base_head)
    monkeypatch.setattr(base_checks, "_clone_at", clone_at)
    monkeypatch.setattr(activities, "_lease", lease)
    monkeypatch.setattr(activities, "_release", release)
    monkeypatch.setattr(sandbox, "run_in", run_in)
    monkeypatch.setattr(events, "emit", lambda kind, payload, **fields: emitted.append((kind, payload, fields)))
    return repo, ran, emitted, released


def _asked(repo: str, task: str, commit: str = "") -> BaseChecksRequest:
    return BaseChecksRequest(repo=repo, task_id=task, goal_id="g-1", image="harness-pi:dev", commit=commit)


async def test_two_tasks_on_one_base_commit_run_each_check_once_and_the_second_is_told_what_fails(base):
    repo, ran, emitted, released = base
    first = await ActivityEnvironment().run(base_checks.known_base_checks, _asked(repo, "t-1"))
    assert (first.commit, first.failing, first.missing) == ("b0b0b0", [], True), "nothing is kept yet"

    found = await ActivityEnvironment().run(base_checks.run_base_checks, _asked(repo, "t-1", first.commit))

    assert ran == CHECKS, "each check, in the registry's order, in one sandbox"
    assert [c.command for c in found.failing] == ["uv run pytest -q"] and found.missing is False
    assert released == ["test-base"], "the sandbox goes when the checks end"
    kinds = [(kind, payload["command"], payload["passed"]) for kind, payload, _ in emitted]
    assert kinds == [("base_check_ran", "uv sync", True), ("base_check_ran", "uv run pytest -q", False)]
    _, payload, fields = emitted[1]
    assert (payload["repo"], payload["commit"], payload["exit_code"], payload["goal_id"]) == (repo, "b0b0b0", 1, "g-1")
    assert fields["task_id"] == "t-1" and fields["tags"] == {"goal_id": "g-1"}

    # A second task on the same commit reads what is kept, and its base check runs nothing.
    second = await ActivityEnvironment().run(base_checks.known_base_checks, _asked(repo, "t-2"))
    assert second.missing is False
    [failing] = second.failing
    assert failing.command == "uv run pytest -q"
    assert failing.output.startswith("[earlier output cut]") and failing.output.endswith("1 failed, 9 passed")
    assert len(failing.output) < 2100, "the end of the output, where a test run says what failed"
    again = await ActivityEnvironment().run(base_checks.run_base_checks, _asked(repo, "t-2", "b0b0b0"))
    assert ran == CHECKS and [c.command for c in again.failing] == ["uv run pytest -q"]
    assert "FAILED tests/test_app.py::test_adds" in base_checks.said_to_run(second.failing)


async def test_a_check_another_task_is_running_is_left_to_it_and_one_given_up_is_run_again(base):
    repo, ran, _, _ = base
    assert base_checks.claim(repo, "b1", CHECKS, "t-1") == CHECKS

    waiting = await ActivityEnvironment().run(base_checks.run_base_checks, _asked(repo, "t-2", "b1"))

    assert ran == [] and waiting.failing == [] and waiting.missing is True, "the other task has them"
    base_checks.let_go(repo, "b1", CHECKS)
    done = await ActivityEnvironment().run(base_checks.run_base_checks, _asked(repo, "t-2", "b1"))
    assert ran == CHECKS and done.missing is False


async def test_base_checks_that_stop_part_way_keep_what_ended_and_give_up_the_rest(base, monkeypatch):
    repo, ran, _, released = base

    async def stops(run_id, command, seconds):
        if "pytest" in command:
            raise sandbox.SandboxError("the docker command is not installed")
        return 0, "ok"

    monkeypatch.setattr(sandbox, "run_in", stops)
    with pytest.raises(sandbox.SandboxError):
        await ActivityEnvironment().run(base_checks.run_base_checks, _asked(repo, "t-1", "b2"))

    kept = base_checks.kept(repo, "b2", CHECKS)
    assert kept.failing == [] and kept.missing is True, "the check that ended is kept; the other has no result"
    assert released, "and the sandbox goes all the same"
    assert base_checks.claim(repo, "b2", CHECKS, "t-2") == ["uv run pytest -q"], "another task runs it"


async def test_a_repo_with_no_standing_checks_is_not_checked(base):
    known = await ActivityEnvironment().run(base_checks.known_base_checks, _asked("owner/unregistered", "t-1"))
    assert (known.commit, known.failing, known.missing) == ("", [], False)


def test_a_check_that_outlasts_its_limit_counts_as_failing_and_says_so(monkeypatch):
    import asyncio

    async def go():
        return await sandbox.run_in("no-such-run", "sleep 5", 0.0)

    class Slow:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(5)

        def kill(self):
            pass

        async def wait(self):
            return None

    async def start(*args, **kwargs):
        return Slow()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    assert asyncio.run(go()) == (124, "the check did not end within 0 seconds, so it was stopped")


def test_a_review_is_told_a_check_failing_on_the_base_does_not_hold_the_pull_request():
    request = LoopPromptRequest(
        kind="review", repo=REPO, title="t", description="d", checks=["pytest -q"], base="main", branch="b"
    )
    assert "base branch" not in activities._checks_rule(request)
    request.base_failing = ["uv run pytest -q"]
    rule = activities._checks_rule(request)
    assert (
        "These checks already fail on the base branch, where they were run before this task: uv run pytest -q" in rule
    )
    assert "does not hold it" in rule and "passes on the base and fails on the pull request blocks" in rule
    assert base_checks.said_to_run([]) == "" and base_checks.said_to_review([]) == ""


async def test_a_run_is_told_what_fails_on_the_base_and_its_review_reads_what_the_base_checks_found(env):  # noqa: F811
    known = FailedBaseCheck("uv run ruff check .", "E501 line too long")
    later = FailedBaseCheck("uv run pytest -q", "found later")
    script = Script(verdicts=[CLEAN], base_checks=[known, later], base_missing=True)

    result = await _run(env, script)

    assert result.outcome == "clean"
    [asked] = script.base_asked_about
    assert (asked.repo, asked.image) == (REPO, script.runs[0].candidate.profile["image"])
    task_prompt = script.runs[0].prompt
    assert task_prompt.startswith("task") and "--- fails on the base: uv run ruff check . ---" in task_prompt
    assert "E501 line too long" in task_prompt and "uv run pytest -q" not in task_prompt, "only what was known then"
    [ran] = script.base_ran
    assert ran.commit == "b0", "the checks with no result ran once, beside the first run"
    assert script.prompts[0].base_failing == ["uv run ruff check .", "uv run pytest -q"]


async def test_a_base_with_nothing_failing_changes_no_prompt_and_runs_nothing(env):  # noqa: F811
    script = Script(verdicts=[CLEAN])

    await _run(env, script)

    assert script.runs[0].prompt == "task" and script.base_ran == []
    assert script.prompts[0].base_failing == []


def _host(monkeypatch, runs: list[code_host.CheckRun], pending: int = 0) -> None:
    async def check_runs(name, commit):
        return runs

    async def pending_suites(name, commit):
        return pending

    monkeypatch.setattr(code_host, "check_runs", check_runs)
    monkeypatch.setattr(code_host, "pending_suites", pending_suites)


def _check(conclusion: str | None, status: str = "completed") -> code_host.CheckRun:
    return code_host.CheckRun(name="tests", status=status, conclusion=conclusion, summary="")


async def test_a_base_the_repos_own_checks_passed_is_kept_as_passing_and_the_sandbox_runs_nothing(base, monkeypatch):
    repo, ran, emitted, _ = base
    _host(monkeypatch, [_check("success"), _check("skipped")])

    known = await ActivityEnvironment().run(base_checks.known_base_checks, _asked(repo, "t-1"))

    assert (known.failing, known.missing) == ([], False), "the workflow starts no base check run"
    assert ran == []
    assert [(p["command"], p["passed"], p["source"]) for _, p, _ in emitted] == [(c, True, "code_host") for c in CHECKS]
    again = await ActivityEnvironment().run(base_checks.known_base_checks, _asked(repo, "t-2"))
    assert again.missing is False and len(emitted) == 2, "kept: the host is not asked to say it twice"


@pytest.mark.parametrize(
    "runs,pending",
    [
        ([_check("success"), _check("failure")], 0),
        ([_check("success"), _check(None, "in_progress")], 0),
        ([_check("success")], 1),
        ([], 0),
    ],
    ids=["one failed", "one still running", "a workflow still queued", "none ran"],
)
async def test_the_code_hosts_word_is_taken_only_for_a_base_every_check_passed(base, monkeypatch, runs, pending):
    repo, ran, emitted, _ = base
    _host(monkeypatch, runs, pending)

    known = await ActivityEnvironment().run(base_checks.known_base_checks, _asked(repo, "t-1"))

    assert known.missing is True and emitted == [], "the sandbox runs the checks, as it did"
