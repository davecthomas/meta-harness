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
    BaseHealth,
    BaseHealthRequest,
    Candidate,
    ChecksRequest,
    CommentRequest,
    ChecksResult,
    CleanupResult,
    FailedCheck,
    HarnessRequest,
    HarnessResult,
    LoopPromptRequest,
    MergeChecksRequest,
    MergeRequest,
    MergeResult,
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
    # The goal's auto_merge, and each merge's result in order (a state, or (state, new head)).
    auto_merge: bool = False
    merges: list = field(default_factory=list)
    # Each merge commit's checks in order: passed, failed, silent (failed with no output), none or pending.
    # None left: passed.
    merge_checks: list[str] = field(default_factory=list)
    # A fix run after a merge changes no file, so its publish opens no pull request.
    fix_changes_nothing: bool = False
    # How long a merge waits for the base's own checks (0: it does not look), and how they stand each time it
    # looks: healthy, running or failed. None left: healthy.
    merge_wait_minutes: int = 0
    # How long a review that asks for changes waits for an operator before its fix run; 0: no wait.
    hold_minutes: int = 0
    comments: list = field(default_factory=list)
    base: list[str] = field(default_factory=list)
    base_asked: list[BaseHealthRequest] = field(default_factory=list)

    runs: list[HarnessRequest] = field(default_factory=list)
    prompts: list[LoopPromptRequest] = field(default_factory=list)
    waited_on: list[str] = field(default_factory=list)
    posted: list[PostReviewRequest] = field(default_factory=list)
    publishes: int = 0
    policy_asked_for: list[str] = field(default_factory=list)
    merge_requests: list[MergeRequest] = field(default_factory=list)
    merge_checked: list[MergeChecksRequest] = field(default_factory=list)
    publish_requests: list[PublishRequest] = field(default_factory=list)
    goals_asked: list[str] = field(default_factory=list)

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
            self.publish_requests.append(request)
            commit = "c0" if self.publishes == 0 else (self.fix_commits.pop(0) if self.fix_commits else "c-new")
            self.publishes += 1
            if self.fix_changes_nothing and self.publishes > 1:
                return PublishResult(
                    branch=request.branch or BRANCH, base="main", commit="", files=[], pr_url="", created=False
                )
            return PublishResult(
                branch=request.branch or BRANCH,
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
            return ChecksResult(state=state, failed=failed, passed=["tests"] if state == "passed" else [])

        @activity.defn(name="wait_merge_checks")
        async def wait_merge_checks(request: MergeChecksRequest) -> ChecksResult:
            self.merge_checked.append(request)
            state = self.merge_checks.pop(0) if self.merge_checks else "passed"
            failed = [FailedCheck("deploy", "terraform apply failed")] if state in ("failed", "pending") else []
            if state == "silent":
                state, failed = "failed", [FailedCheck("deploy", "")]
            return ChecksResult(state=state, failed=failed)

        @activity.defn(name="review_policy")
        async def review_policy(author_model: str) -> ReviewPolicy:
            self.policy_asked_for.append(author_model)
            reviewers = [r for r in self.reviewers if f"{r.engine}:{r.model}" != author_model]
            return ReviewPolicy(
                reviewers=reviewers,
                max_rounds=self.max_rounds,
                unresolved=self.unresolved,
                merge_wait_minutes=self.merge_wait_minutes,
                hold_minutes=self.hold_minutes,
            )

        @activity.defn(name="post_comment")
        async def post_comment(request: CommentRequest) -> str:
            self.comments.append(request)
            return "https://github.com/owner/repo/pull/7#comment"

        @activity.defn(name="base_health")
        async def base_health(request: BaseHealthRequest) -> BaseHealth:
            self.base_asked.append(request)
            state = self.base.pop(0) if self.base else "healthy"
            return BaseHealth(state=state, sha="b0b0b0b0b0b0", checks=[] if state == "healthy" else ["deploy"])

        @activity.defn(name="loop_prompt")
        async def loop_prompt(request: LoopPromptRequest) -> str:
            self.prompts.append(request)
            return request.kind

        @activity.defn(name="post_review")
        async def post_review(request: PostReviewRequest) -> str:
            self.posted.append(request)
            return f"https://github.com/{REPO}/pull/7#review-{request.round_no}"

        @activity.defn(name="goal_auto_merge")
        async def goal_auto_merge(goal_id: str) -> bool:
            self.goals_asked.append(goal_id)
            return self.auto_merge

        @activity.defn(name="merge_pr")
        async def merge_pr(request: MergeRequest) -> MergeResult:
            self.merge_requests.append(request)
            answer = self.merges.pop(0) if self.merges else "merged"
            # A tuple is (state, new head), with "same" third when the pull request's own diff did not change.
            state, head, *same = answer if isinstance(answer, tuple) else (answer, request.head_sha)
            return MergeResult(
                state=state,
                merge_sha="m1" if state == "merged" else "",
                head_sha=head,
                detail="x",
                same_diff=bool(same),
            )

        return [
            goal_auto_merge,
            merge_pr,
            prepare,
            route,
            run_harness,
            cleanup_run,
            publish,
            wait_checks,
            wait_merge_checks,
            base_health,
            post_comment,
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


async def _run(env: WorkflowEnvironment, script: Script, spend: str = "10.00", review: bool = True, goal_id=""):
    async with Worker(env.client, task_queue=QUEUE, workflows=[RunTask], activities=script.activities()):
        return await env.client.execute_workflow(
            RunTask.run,
            RunTaskInput(task="Fix the bug.", repo=REPO, spend_limit_usd=spend, review=review, goal_id=goal_id),
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


# --- The merge ----------------------------------------------------------------------------------------


async def test_a_clean_pull_request_merges_at_the_reviewed_commit_when_its_goal_allows(env):
    script = Script(verdicts=[CLEAN], auto_merge=True)

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "merged"
    assert script.goals_asked == ["g-1"]
    merge = script.merge_requests[0]
    assert (merge.head_sha, merge.base, merge.branch) == ("c0", "main", BRANCH)
    assert "merged as m1" in result.outcome_reason


async def test_a_failed_check_on_the_merge_commit_becomes_a_fix_pull_request_that_merges(env):
    script = Script(verdicts=[CLEAN, CLEAN], auto_merge=True, merge_checks=["failed", "passed"])

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "merged"
    assert [c.merge_sha for c in script.merge_checked] == ["m1", "m1"]
    assert script.merge_checked[0].base == "main"
    fix_run = script.runs[2]
    assert fix_run.branch == "", "the fix after a merge starts from the base, not the merged branch"
    fix_prompt = next(p for p in script.prompts if p.merged_pr)
    assert fix_prompt.merged_pr == f"https://github.com/{REPO}/pull/7"
    assert [f.name for f in fix_prompt.failed_checks] == ["deploy"]
    fix_pr = script.publish_requests[1]
    assert fix_pr.branch.endswith("-merge-1") and fix_pr.branch.startswith("mh/")
    assert fix_pr.ticket == "", "the issue closed with the first merge, so the fix closes nothing"
    assert "deploy failed on its merge commit" in fix_pr.note
    assert len(script.merge_requests) == 2, "the fix pull request went through the review loop and merged"
    assert "the merge commit's checks: passed" in result.outcome_reason


async def test_a_merge_check_that_failed_with_no_output_starts_no_fix_run(env):
    script = Script(verdicts=[CLEAN], auto_merge=True, merge_checks=["silent"])

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "merge_checks_failed"
    assert "deploy failed on the merge commit m1 with no output a fix run could act on" in result.outcome_reason
    assert _kinds(result) == ["standard", "review"], "no fix run"


async def test_a_fix_that_changed_nothing_looks_at_the_merge_checks_once_more(env):
    passed = Script(verdicts=[CLEAN], auto_merge=True, merge_checks=["failed", "passed"], fix_changes_nothing=True)
    result = await _run(env, passed, goal_id="g-1")
    assert result.outcome == "merged", "a newer commit's passing run settled it"
    assert len(passed.merge_checked) == 2 and _kinds(result).count("fix") == 1

    failed = Script(verdicts=[CLEAN], auto_merge=True, merge_checks=["failed", "failed"], fix_changes_nothing=True)
    result = await _run(env, failed, goal_id="g-1")
    assert result.outcome == "merge_checks_failed" and "its fix changed nothing" in result.outcome_reason
    assert _kinds(result).count("fix") == 1, "no second fix run"


async def test_failures_are_logged_as_warnings_that_name_the_task_and_the_run(env, caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="temporalio.workflow")
    script = Script(verdicts=[None], auto_merge=True)

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "review_unresolved"
    warned = [r.getMessage() for r in caplog.records if r.name == "temporalio.workflow" and r.levelname == "WARNING"]
    assert any(
        ":run-2: review run 2 on " in w and "ended with no verdict" in w and w.startswith("task ") for w in warned
    )

    caplog.clear()
    script = Script(verdicts=[CLEAN], auto_merge=True, merge_checks=["silent"])
    await _run(env, script, goal_id="g-1")
    warned = [r.getMessage() for r in caplog.records if r.name == "temporalio.workflow" and r.levelname == "WARNING"]
    assert any("checks on the merge commit m1 failed: deploy" in w for w in warned)


async def test_a_merge_commit_with_no_checks_ends_merged(env):
    script = Script(verdicts=[CLEAN], auto_merge=True, merge_checks=["none"])

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "merged" and "the merge commit's checks: none ran" in result.outcome_reason
    assert script.publishes == 1


async def test_merge_checks_still_running_after_the_wait_fail_the_task(env):
    script = Script(verdicts=[CLEAN], auto_merge=True, merge_checks=["pending"])

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "merge_checks_failed"
    assert "did not finish in 120 minutes: deploy" in result.outcome_reason


async def test_a_merge_commit_that_keeps_failing_uses_up_the_fix_rounds(env):
    script = Script(verdicts=[CLEAN, CLEAN], auto_merge=True, merge_checks=["failed", "failed"], max_rounds=1)

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "merge_checks_failed"
    assert "the 1 fix rounds are used up" in result.outcome_reason


async def test_with_auto_merge_off_a_clean_pull_request_waits_for_an_operator(env):
    script = Script(verdicts=[CLEAN], auto_merge=False)

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "clean"
    assert script.merge_requests == []
    assert "auto_merge off" in result.outcome_reason


async def test_a_task_with_no_goal_never_merges_and_never_asks(env):
    script = Script(verdicts=[CLEAN], auto_merge=True)

    result = await _run(env, script)

    assert result.outcome == "clean" and script.goals_asked == [] and script.merge_requests == []
    assert "no goal" in result.outcome_reason


async def test_a_commit_after_the_review_is_checked_and_reviewed_before_it_merges(env):
    script = Script(verdicts=[CLEAN, CLEAN], auto_merge=True, merges=[("head_changed", "c9"), "merged"])

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "merged"
    assert script.waited_on == ["c0", "c9"]
    assert [p.head_sha for p in script.posted] == ["c0", "c9"]
    assert [m.head_sha for m in script.merge_requests] == ["c0", "c9"]


async def test_the_base_brought_in_without_changing_the_diff_reruns_the_checks_and_merges_with_no_new_review(env):
    script = Script(verdicts=[CLEAN], auto_merge=True, merges=[("head_changed", "c9", "same"), "merged"])

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "merged"
    assert script.waited_on == ["c0", "c9"], "the new head's checks run"
    assert [p.head_sha for p in script.posted] == ["c0"], "and it is not reviewed again"
    assert [m.head_sha for m in script.merge_requests] == ["c0", "c9"]
    assert "main was brought in without changing the pull request's diff" in result.outcome_reason


async def test_a_standing_clean_review_is_dropped_when_the_new_heads_checks_fail(env):
    script = Script(
        verdicts=[CLEAN, CLEAN],
        auto_merge=True,
        merges=[("head_changed", "c9", "same"), "merged"],
        checks={"c9": "failed"},
        fix_commits=["c10"],
    )

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "merged"
    assert [p.head_sha for p in script.posted] == ["c0", "c10"], "the fix's commit is reviewed"


async def test_a_review_after_the_first_is_given_the_round_befores_findings(env):
    script = Script(verdicts=[CHANGES, CLEAN], fix_commits=["c1"])

    await _run(env, script)

    first, second = [p for p in script.prompts if p.kind == "review"]
    assert first.previous_findings == [] and first.round_no == 1
    assert second.round_no == 2 and second.previous_findings == CHANGES["findings"]
    assert second.previous_summary == CHANGES["summary"]
    assert first.passed_checks == ["tests"] == second.passed_checks, "each review is told the checks that passed"


async def test_a_merge_waits_until_the_bases_own_checks_have_passed(env):
    script = Script(verdicts=[CLEAN], auto_merge=True, merge_wait_minutes=30, base=["running", "running", "healthy"])

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "merged"
    assert len(script.base_asked) == 3 and script.base_asked[0].base == "main"
    assert len(script.merge_requests) == 1, "it merged once, after the base was healthy"


async def test_a_base_that_stays_broken_leaves_the_pull_request_open_for_an_operator(env):
    script = Script(verdicts=[CLEAN], auto_merge=True, merge_wait_minutes=2, base=["failed"] * 5)

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "clean" and not script.merge_requests, "nothing merges onto a broken base"
    assert "main's checks at b0b0b0b0b0b0 (deploy) have failed after 2 minutes" in result.outcome_reason
    assert "merge it once they pass" in result.outcome_reason


async def test_a_fix_for_the_bases_own_failed_check_merges_without_waiting_for_the_base(env):
    script = Script(
        verdicts=[CLEAN, CLEAN],
        auto_merge=True,
        merge_checks=["failed", "passed"],
        merge_wait_minutes=30,
        base=["healthy", "failed"],
    )

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "merged" and len(script.merge_requests) == 2
    assert len(script.base_asked) == 1, "the first merge looked; the fix that repairs the base did not"


async def test_a_policy_with_no_wait_merges_without_looking_at_the_base(env):
    script = Script(verdicts=[CLEAN], auto_merge=True, base=["failed"])

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "merged" and not script.base_asked


async def test_a_conflict_goes_to_a_fix_run_that_resolves_the_merge_then_merges(env):
    script = Script(verdicts=[CLEAN, CLEAN], auto_merge=True, merges=["conflict", "merged"], fix_commits=["c1"])

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "merged"
    assert _kinds(result) == ["standard", "review", "fix", "review"]
    fix_run = script.runs[2]
    assert fix_run.merge_base == "main", "the host merges the base into the fix run's clone"
    fix_prompt = next(p for p in script.prompts if p.kind == "fix")
    assert fix_prompt.conflict is True
    assert [m.head_sha for m in script.merge_requests] == ["c0", "c1"]


async def test_a_pull_request_github_refuses_to_merge_ends_ready_with_the_reason(env):
    script = Script(verdicts=[CLEAN], auto_merge=True, merges=["blocked"])

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "clean"
    assert "would not merge" in result.outcome_reason


async def test_an_unreviewed_pull_request_never_merges(env):
    script = Script(reviewers=[], unresolved=["no reviewer"], auto_merge=True)

    result = await _run(env, script, goal_id="g-1")

    assert result.outcome == "unreviewed"
    assert script.merge_requests == [] and script.goals_asked == []
