"""The review loop on a task's pull request, against stand-in activities on Temporal's time-skipping test server.

Each test scripts the checks on each head commit, each review run's verdict,
and what each fix run pushes, then checks how the loop ends: clean, after
fixes, or review_unresolved at a limit.
"""

import uuid
from dataclasses import dataclass, field

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from meta_harness.run_service.shared import (
    Candidate,
    ChecksRequest,
    ChecksResult,
    CleanupResult,
    FailedCheck,
    HarnessRequest,
    HarnessResult,
    LoopPromptRequest,
    PostReviewRequest,
    PreparedRun,
    PublishRequest,
    PublishResult,
    ReviewPolicy,
    RouteRequest,
    RouteResult,
    RunTaskInput,
)
from meta_harness.run_service.workflows import RunTask

QUEUE = "test-review-loop"
REPO = "owner/repo"
BRANCH = "mh/test"


def _pair(profile: str, engine: str, model: str) -> Candidate:
    return Candidate(
        label=f"harness:{profile}/{engine}:{model}",
        profile_name=profile,
        engine=engine,
        model=model,
        profile={"name": profile, "tools": ["read"], "max_turns": 10, "max_minutes": 30, "image": "x"},
    )


AUTHOR = _pair("pi-lean", "claude", "sonnet")
REVIEWER_A = _pair("pi-review", "claude", "opus")
REVIEWER_B = _pair("pi-review", "openai", "sol")
CLEAN = {"verdict": "clean", "summary": "Looks right.", "findings": []}
CHANGES = {
    "verdict": "changes",
    "summary": "One bug.",
    "findings": [{"path": "app.py", "line": 3, "body": "Off by one."}],
}


@dataclass
class Script:
    """The loop's world: checks per head commit, verdicts per review, commits per fix, and what was asked."""

    # The checks' state on each head commit; a head not listed passes.
    checks: dict[str, str] = field(default_factory=dict)
    # Each review run's verdict, in order: a dict, or None for a run that ends without one.
    verdicts: list[dict | None] = field(default_factory=list)
    # The commit each fix run's publish returns, in order. The first publish (the PR) returns "c0".
    fix_commits: list[str] = field(default_factory=list)
    reviewers: list[Candidate] = field(default_factory=lambda: [REVIEWER_A, REVIEWER_B])
    max_rounds: int = 3
    unresolved: list[str] = field(default_factory=list)
    run_cost: str = "1.00"

    runs: list[HarnessRequest] = field(default_factory=list)
    prompts: list[LoopPromptRequest] = field(default_factory=list)
    waited_on: list[str] = field(default_factory=list)
    posted: list[PostReviewRequest] = field(default_factory=list)
    publishes: int = 0
    policy_asked_for: list[str] = field(default_factory=list)

    def activities(self):
        @activity.defn(name="prepare")
        async def prepare(request: RunTaskInput) -> PreparedRun:
            return PreparedRun(
                template="repo-task-v1",
                prompt="task",
                repo=REPO,
                title="Fix the bug",
                description="The bug is in app.py.",
                checks=["pytest -q"],
            )

        @activity.defn(name="route")
        async def route(request: RouteRequest) -> RouteResult:
            return RouteResult(
                tier="standard",
                method="classifier",
                confidence=0.9,
                candidates=[AUTHOR],
                tiers=["standard"],
                on_exhausted="fail",
                classifier_usd="0",
                config_id="c",
                thinking="medium",
            )

        @activity.defn(name="run_harness")
        async def run_harness(request: HarnessRequest) -> HarnessResult:
            self.runs.append(request)
            review = None
            if request.prompt == "review":
                review = self.verdicts.pop(0)
            return HarnessResult(
                attempt=1,
                run_id=request.run_id,
                status="FINISHED",
                end_reason="completed",
                reattached=False,
                turns=3,
                failed_calls=0,
                tokens_in=10,
                tokens_out=5,
                usd=1.0,
                files_changed=[],
                answer="done",
                spend_limit_usd=request.spend_limit_usd,
                grant_spent_usd=self.run_cost,
                review=review,
            )

        @activity.defn(name="cleanup_run")
        async def cleanup_run(run_id: str) -> CleanupResult:
            return CleanupResult(grant_spent_usd="0")

        @activity.defn(name="publish")
        async def publish(request: PublishRequest) -> PublishResult:
            commit = "c0" if self.publishes == 0 else self.fix_commits.pop(0)
            self.publishes += 1
            return PublishResult(
                branch=BRANCH,
                base="main",
                commit=commit,
                files=["app.py"],
                pr_url=f"https://github.com/{REPO}/pull/7",
                created=self.publishes == 1,
            )

        @activity.defn(name="wait_checks")
        async def wait_checks(request: ChecksRequest) -> ChecksResult:
            self.waited_on.append(request.head_sha)
            state = self.checks.get(request.head_sha, "passed")
            failed = [FailedCheck("tests", "1 failed: test_app")] if state == "failed" else []
            return ChecksResult(state=state, failed=failed)

        @activity.defn(name="review_policy")
        async def review_policy(author_model: str) -> ReviewPolicy:
            self.policy_asked_for.append(author_model)
            reviewers = [r for r in self.reviewers if f"{r.engine}:{r.model}" != author_model]
            return ReviewPolicy(reviewers=reviewers, max_rounds=self.max_rounds, unresolved=self.unresolved)

        @activity.defn(name="loop_prompt")
        async def loop_prompt(request: LoopPromptRequest) -> str:
            self.prompts.append(request)
            return request.kind

        @activity.defn(name="post_review")
        async def post_review(request: PostReviewRequest) -> str:
            self.posted.append(request)
            return f"https://github.com/{REPO}/pull/7#review-{request.round_no}"

        return [
            prepare,
            route,
            run_harness,
            cleanup_run,
            publish,
            wait_checks,
            review_policy,
            loop_prompt,
            post_review,
        ]


@pytest.fixture
async def env():
    try:
        environment = await WorkflowEnvironment.start_time_skipping()
    except Exception as err:  # noqa: BLE001 - the test server is a download; without it these tests cannot run
        pytest.skip(f"Temporal's time-skipping test server is not available: {err}")
    async with environment:
        yield environment


async def _run(env: WorkflowEnvironment, script: Script, spend: str = "10.00", review: bool = True):
    async with Worker(env.client, task_queue=QUEUE, workflows=[RunTask], activities=script.activities()):
        return await env.client.execute_workflow(
            RunTask.run,
            RunTaskInput(task="Fix the bug.", repo=REPO, spend_limit_usd=spend, review=review),
            id=f"run-task-test-{uuid.uuid4().hex[:8]}",
            task_queue=QUEUE,
        )


def _kinds(result) -> list[str | None]:
    return [a.tier for a in result.attempts]


async def test_a_clean_first_review_by_another_model_ends_the_loop_clean(env):
    script = Script(verdicts=[CLEAN])

    result = await _run(env, script)

    assert result.outcome == "clean"
    assert _kinds(result) == ["standard", "review"]
    # The author's model asked for its reviewers, so the policy can leave it out.
    assert script.policy_asked_for == ["claude:sonnet"]
    review_run = script.runs[1]
    assert review_run.candidate.label == REVIEWER_A.label
    assert review_run.branch == BRANCH, "a review run clones the pull request's branch"
    assert review_run.thinking is None
    assert [p.round_no for p in script.posted] == [1]
    assert script.posted[0].head_sha == "c0" and script.posted[0].review == CLEAN
    review_prompt = script.prompts[0]
    assert (review_prompt.title, review_prompt.description, review_prompt.checks) == (
        "Fix the bug",
        "The bug is in app.py.",
        ["pytest -q"],
    )
    assert (review_prompt.base, review_prompt.branch) == ("main", BRANCH)


async def test_changes_go_to_a_fix_on_the_author_and_the_new_head_is_checked_and_reviewed(env):
    script = Script(verdicts=[CHANGES, CLEAN], fix_commits=["c1"])

    result = await _run(env, script)

    assert result.outcome == "clean"
    assert _kinds(result) == ["standard", "review", "fix", "review"]
    fix_run = script.runs[2]
    assert fix_run.candidate.label == AUTHOR.label
    assert fix_run.branch == BRANCH
    assert fix_run.thinking == "medium", "a fix run keeps the routed tier's thinking"
    fix_prompt = next(p for p in script.prompts if p.kind == "fix")
    assert fix_prompt.findings == CHANGES["findings"] and fix_prompt.summary == "One bug."
    assert fix_prompt.round_no == 1
    assert script.waited_on == ["c0", "c1"]
    assert [(p.round_no, p.head_sha) for p in script.posted] == [(1, "c0"), (2, "c1")]
    assert "round 2" in result.outcome_reason


async def test_failed_checks_go_to_a_fix_with_the_check_output_before_any_review(env):
    script = Script(checks={"c0": "failed"}, verdicts=[CLEAN], fix_commits=["c1"])

    result = await _run(env, script)

    assert result.outcome == "clean"
    assert _kinds(result) == ["standard", "fix", "review"]
    fix_prompt = script.prompts[0]
    assert fix_prompt.kind == "fix"
    assert [c.name for c in fix_prompt.failed_checks] == ["tests"]
    assert script.waited_on == ["c0", "c1"]


async def test_the_loop_stops_unresolved_after_max_rounds_of_fixes(env):
    script = Script(verdicts=[CHANGES, CHANGES, CHANGES], fix_commits=["c1", "c2"], max_rounds=2)

    result = await _run(env, script)

    assert result.outcome == "review_unresolved"
    assert "2 fix rounds" in result.outcome_reason
    assert _kinds(result) == ["standard", "review", "fix", "review", "fix", "review"]


async def test_a_fix_that_changes_nothing_ends_the_loop(env):
    script = Script(verdicts=[CHANGES], fix_commits=["c0"])

    result = await _run(env, script)

    assert result.outcome == "review_unresolved"
    assert "changed nothing" in result.outcome_reason


async def test_a_reviewer_with_no_verdict_hands_the_round_to_the_next_reviewer(env):
    script = Script(verdicts=[None, CLEAN])

    result = await _run(env, script)

    assert result.outcome == "clean"
    assert [r.candidate.label for r in script.runs[1:]] == [REVIEWER_A.label, REVIEWER_B.label]
    assert script.posted[0].reviewer == REVIEWER_B.label


async def test_no_reviewer_leaves_the_pull_request_unreviewed_with_the_reason(env):
    script = Script(reviewers=[], unresolved=["harness:pi-review/claude:opus: unknown harness profile pi-review"])

    result = await _run(env, script)

    assert result.outcome == "unreviewed"
    assert "unknown harness profile pi-review" in result.outcome_reason
    assert _kinds(result) == ["standard"]
    assert script.posted == []


async def test_the_spend_limit_stops_the_loop(env):
    # The first run spends $2 of $3, a review spends the rest, and no fix can start.
    script = Script(verdicts=[CHANGES], run_cost="1.50")

    result = await _run(env, script, spend="3.00")

    assert result.outcome == "review_unresolved"
    assert "spend limit" in result.outcome_reason
    assert _kinds(result) == ["standard", "review"]


async def test_with_the_loop_off_the_pull_request_is_left_as_opened(env):
    script = Script()

    result = await _run(env, script, review=False)

    assert result.outcome == ""
    assert _kinds(result) == ["standard"]
    assert script.waited_on == []
