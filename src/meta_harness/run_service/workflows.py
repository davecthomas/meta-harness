"""The RunTask workflow: prepare, route, then the run loop over the tier's candidates.

The design's section 6.3. The route activity asks the ai-api-unified router
for the task's tier and the tier's candidates, each a harness profile and a
model. The workflow runs one run_harness activity per candidate, and its
Decide step reads how each run ended:

- FINISHED ends the loop, and the task completes.
- An infrastructure failure tries the next candidate: no per-run sandbox, a
  lost harness server, a run grant the HTTP Gateway could not issue, or a run
  that ended model_error, harness_error or server_shutdown.
- A run that ended on a limit (max_turns, max_minutes, spend_limit) is final,
  and so is a refused configuration. Trying another candidate after a run
  spent its budget would spend it again.

When a tier's candidates are used up and the router's on_exhausted is
escalate, the workflow routes again one tier up, under the task's ceiling.
A pinned task runs its one pair, with no routing and no escalation.

A repo task's runs each start from a fresh clone of the repo. When a run
finishes, the publish activity commits pi's changes to the branch mh/<REF>,
pushes it and opens the pull request. A finished run that changed nothing
fails the task.

Then the review loop (docs/technical-design.md, Review loop) runs on the pull
request, in this workflow:

1. wait_checks waits for the code host's checks on the head commit.
2. Failed checks go to a fix run. Passing checks, or none, go to a review run
   on the first reviewer pair in config/task_policy.json whose model did not
   write the pull request, on the pull request's branch. Its submit_review
   verdict is posted on the pull request.
3. A clean verdict ends the loop. A changes verdict goes to a fix run: the
   pair that wrote the pull request, in a new pi session on the branch, whose
   publish pushes to the same branch. Then back to step 1.

4. After a clean review, when the task's goal has auto_merge on, merge_pr
   merges the pull request at the reviewed head commit. A commit that landed
   after the review goes back to step 1, so it is checked and reviewed too. A
   conflict with the base goes to a fix run that resolves the merge the host
   started, and back to step 1.

The loop ends review_unresolved after max_rounds fix runs, when the task's
spend limit is used up, when a fix run changes nothing, or when no reviewer
pair returns a verdict. A merged pull request ends the task closed. A clean
one that does not merge (auto_merge off, no goal, or GitHub refusing) ends it
ready, with the reason.

The task's spend limit covers all its runs: each run's grant gets what the
earlier runs left. A run run_harness gave up on still counts, from what
cleanup_run reads off its grant.

Workflow code runs in the SDK's sandbox and must be deterministic: no I/O, no
clocks, no subprocesses. All of that lives in the activities. The activity and
payload modules are passed through the sandbox so it does not reload them.

workflow.logger skips its output while the worker replays history, so each
step logs once, when it first runs. A worker that picks the workflow up from
history prints one line saying it replayed.
"""

import asyncio
from datetime import timedelta
from decimal import Decimal

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError, TimeoutError

with workflow.unsafe.imports_passed_through():
    from meta_harness.run_service.activities import (
        cleanup_run,
        deliver_message,
        goal_auto_merge,
        loop_prompt,
        merge_pr,
        post_review,
        prepare,
        publish,
        review_policy,
        route,
        run_harness,
        wait_checks,
    )
    from meta_harness.run_service.shared import (
        Attempt,
        DeliverRequest,
        OperatorMessage,
        ChecksRequest,
        LoopPromptRequest,
        MergeRequest,
        MergeResult,
        PostReviewRequest,
        AttemptRecord,
        Candidate,
        PublishedRecord,
        RouteRecord,
        TaskStatusRequest,
        HarnessRequest,
        HarnessResult,
        PreparedRun,
        PublishRequest,
        PublishResult,
        RouteRequest,
        RouteResult,
        RunTaskInput,
        RunTaskResult,
        run_id_for,
    )
    from meta_harness.run_service.task_activities import (
        load_task,
        record_attempt,
        record_published,
        record_route,
        set_task_status,
    )

TASK_API_TIMEOUT = timedelta(seconds=30)
# A refused call fails at once (task_activities marks it non-retryable); an unreachable task service is retried.
TASK_API_RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), maximum_attempts=10)
RECORD_TIMEOUT = timedelta(seconds=30)
RECORD_RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), maximum_attempts=10)
PREPARE_TIMEOUT = timedelta(seconds=30)
PREPARE_RETRY = RetryPolicy(maximum_attempts=3)
ROUTE_TIMEOUT = timedelta(seconds=60)
ROUTE_RETRY = RetryPolicy(maximum_attempts=3, non_retryable_error_types=["HarnessConfigError"])
CLEANUP_TIMEOUT = timedelta(seconds=60)
PUBLISH_TIMEOUT = timedelta(minutes=2)
PUBLISH_RETRY = RetryPolicy(initial_interval=timedelta(seconds=5), maximum_attempts=3)
# A run_harness attempt may take its profile's max_minutes plus this much: the clone, the per-run
# sandbox's start, and the Collect step after pi stops at its own limit.
HARNESS_GRACE = timedelta(minutes=10)
# The limit for a profile that names no max_minutes.
DEFAULT_RUN_MINUTES = 30
# A worker that stops heartbeating for this long is treated as gone, and the attempt is retried.
HARNESS_HEARTBEAT_TIMEOUT = timedelta(seconds=10)
HARNESS_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_attempts=3,
    non_retryable_error_types=["HarnessConfigError"],
)
# run_finished end reasons the next candidate may get past: the infrastructure failed, not the task.
NEXT_CANDIDATE_END_REASONS = frozenset({"model_error", "harness_error", "server_shutdown"})
# run_harness failures the next candidate may get past. A failed clone (CodeHostError) is the repo's, not the candidate's.
NEXT_CANDIDATE_ERROR_TYPES = frozenset({"SandboxUnavailable", "HarnessUnavailable", "GatewayUnavailable"})
# The review loop's steps. A repo's checks may take a while; wait_checks heartbeats while it polls.
CHECKS_TIMEOUT = timedelta(minutes=75)
CHECKS_HEARTBEAT_TIMEOUT = timedelta(minutes=2)
CODE_HOST_TIMEOUT = timedelta(minutes=2)
CODE_HOST_RETRY = RetryPolicy(initial_interval=timedelta(seconds=5), maximum_attempts=5)


@workflow.defn
class RunTask:
    def __init__(self) -> None:
        self._current_run_id: str | None = None
        self._task_id = ""
        self._route_no = 0
        # Operators' messages to the run in progress, in the order they came, until each is handed over.
        self._messages: list[OperatorMessage] = []

    @workflow.signal
    def operator_message(self, message: OperatorMessage) -> None:
        """An operator's message for the run in progress, or stop. The scheduler sends it from the task service."""
        self._messages.append(message)

    async def _deliver_messages(self) -> None:
        """Hands each operator message to the run in progress, as it comes, for the life of the workflow."""
        while True:
            await workflow.wait_condition(lambda: bool(self._messages))
            message = self._messages.pop(0)
            if not self._current_run_id:
                workflow.logger.info("operator message %s: no run in progress", message.message_id)
                continue
            try:
                said = await workflow.execute_activity(
                    deliver_message,
                    DeliverRequest(run_id=self._current_run_id, message=message),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                workflow.logger.info("operator message %s: %s", message.message_id, said)
            except ActivityError as err:
                workflow.logger.warning("operator message %s not delivered: %s", message.message_id, err.cause or err)

    @workflow.query
    def current_run_id(self) -> str | None:
        """The run the task is on now, so `make watch` and `make status` find its per-run sandbox."""
        return self._current_run_id

    @workflow.run
    async def run(self, request: RunTaskInput) -> RunTaskResult:
        """A task from the Task API, or a task the caller's REF and text describe.

        A Task API task is loaded from the task service and moved through its
        statuses: running while its runs go, then ready with its pull request,
        failed, or canceled. Its routing and runs are recorded as they happen.
        """
        if not request.task_id:
            return await self._run_loop(request)
        self._task_id = request.task_id
        # Behind a patch: workflows started before it have no message delivery, and replay as they ran.
        if workflow.patched("operator-messages"):
            asyncio.ensure_future(self._deliver_messages())
        spec = await workflow.execute_activity(
            load_task, request.task_id, start_to_close_timeout=TASK_API_TIMEOUT, retry_policy=TASK_API_RETRY
        )
        request = RunTaskInput(
            task=spec.description,
            title=spec.title,
            repo=spec.repo,
            checks=spec.checks,
            pin=spec.pin,
            floor=spec.floor,
            ceiling=spec.ceiling,
            spend_limit_usd=spec.spend_limit_usd,
            closes=spec.closes,
            task_id=spec.task_id,
            goal_id=spec.goal_id,
        )
        await self._task_status("running", "the meta-harness run service started its RunTask workflow")
        try:
            result = await self._run_loop(request)
        except asyncio.CancelledError:
            await self._task_status("canceled", "the RunTask workflow was canceled")
            raise
        except ActivityError as err:
            if isinstance(err.cause, CancelledError):
                await self._task_status("canceled", "the RunTask workflow was canceled")
            else:
                await self._task_status("failed", str(err.cause or err)[:500])
            raise
        except ApplicationError as err:
            await self._task_status("failed", str(err)[:500])
            raise
        pr_url = result.published.pr_url if result.published else ""
        if result.outcome == "review_unresolved":
            await self._task_status("review_unresolved", result.outcome_reason, pr_url)
        elif result.outcome == "merged":
            await self._task_status("closed", result.outcome_reason, pr_url)
        elif pr_url:
            await self._task_status("ready", result.outcome_reason or "the run finished", pr_url)
        else:
            await self._task_status("closed", "the run finished")
        return result

    async def _task_status(self, status: str, reason: str, pr_url: str = "") -> None:
        await workflow.execute_activity(
            set_task_status,
            TaskStatusRequest(task_id=self._task_id, status=status, reason=reason, pr_url=pr_url),
            start_to_close_timeout=TASK_API_TIMEOUT,
            retry_policy=TASK_API_RETRY,
        )

    async def _record(self, activity_fn, record) -> None:
        await workflow.execute_activity(
            activity_fn, record, start_to_close_timeout=RECORD_TIMEOUT, retry_policy=RECORD_RETRY
        )

    async def _run_loop(self, request: RunTaskInput) -> RunTaskResult:
        info = workflow.info()
        log = workflow.logger
        if workflow.unsafe.is_replaying():
            # workflow.logger is silent during replay, so this one line uses print.
            print(
                f"[workflow] {info.workflow_id}: this worker has no state for the workflow, "
                "so it replays the history to rebuild it. Steps already in the history do not run again.",
                flush=True,
            )
        log.info(
            "%s: started, run id %s, %s, %s, spend limit $%s",
            info.workflow_id,
            info.run_id,
            "task text from the caller" if request.task else f"template {request.template}",
            f"pinned to {request.pin}" if request.pin else _hints(request),
            request.spend_limit_usd,
        )
        prepared = await workflow.execute_activity(
            prepare, request, start_to_close_timeout=PREPARE_TIMEOUT, retry_policy=PREPARE_RETRY
        )
        log.info("%s: prepare returned a %d-character prompt", info.workflow_id, len(prepared.prompt))

        limit = Decimal(request.spend_limit_usd)
        spent = Decimal(0)
        attempts: list[Attempt] = []
        route_result: RouteResult | None = None
        floor = request.floor
        while True:
            if prepared.pinned:
                tier: str | None = None
                candidates = [prepared.pinned]
            else:
                route_result = await self._route(prepared.prompt, floor, request.ceiling)
                tier, candidates = route_result.tier, route_result.candidates
                if self._task_id:
                    self._route_no += 1
                    await self._record(
                        record_route, RouteRecord(self._task_id, self._route_no, floor, request.ceiling, route_result)
                    )
            for candidate in candidates:
                left = limit - spent
                if left <= 0:
                    raise _failed(f"the task spent its ${limit} spend limit after {len(attempts)} runs", attempts)
                attempt = len(attempts) + 1
                run_id = run_id_for(info.workflow_id, attempt)
                self._current_run_id = run_id
                harness, decision, detail, run_spent = await self._try(
                    prepared.prompt,
                    run_id,
                    candidate,
                    left,
                    prepared.repo,
                    route_result.thinking if route_result is not None and not prepared.pinned else None,
                )
                spent += run_spent
                attempts.append(
                    Attempt(
                        attempt=attempt,
                        tier=tier,
                        candidate=candidate.label,
                        run_id=run_id,
                        decision=decision,
                        detail=detail,
                        spent_usd=str(run_spent),
                    )
                )
                if self._task_id:
                    await self._record(record_attempt, AttemptRecord(self._task_id, attempts[-1], candidate, harness))
                log.info(
                    "%s: run %d on %s: %s, so the Decide step says %s. Spent $%s of $%s",
                    info.workflow_id,
                    attempt,
                    candidate.label,
                    detail,
                    decision,
                    spent,
                    limit,
                )
                if decision == "finished" and harness is not None:
                    published = await self._publish(prepared, harness, candidate, spent) if prepared.repo else None
                    if published is not None and not published.pr_url:
                        raise _failed(f"run {attempt} finished, but pi changed nothing in {prepared.repo}", attempts)
                    if self._task_id and published is not None:
                        await self._record(record_published, PublishedRecord(self._task_id, published))
                    outcome, reason = "", ""
                    if published is not None and request.review:
                        thinking = route_result.thinking if route_result is not None and not prepared.pinned else None
                        loop = _Loop(request, prepared, candidate, published, limit, spent, attempts, thinking)
                        outcome, reason = await self._review_loop(loop)
                        spent = loop.spent
                    log.info(
                        "%s: completed%s", info.workflow_id, f", review loop {outcome}: {reason}" if outcome else ""
                    )
                    return RunTaskResult(
                        template=prepared.template,
                        prompt=prepared.prompt,
                        route=route_result,
                        attempts=attempts,
                        harness=harness,
                        spent_usd=str(spent),
                        published=published,
                        outcome=outcome,
                        outcome_reason=reason,
                    )
                if decision == "final":
                    raise _failed(f"run {attempt} on {candidate.label} ended for good: {detail}", attempts)
            next_tier = self._escalation(route_result, request) if not prepared.pinned else None
            if next_tier is None:
                raise _failed(f"every candidate failed ({len(attempts)} runs)", attempts)
            log.info(
                "%s: tier %s has no candidates left, so the task escalates to %s", info.workflow_id, tier, next_tier
            )
            floor = next_tier

    async def _route(self, prompt: str, floor: str, ceiling: str) -> RouteResult:
        info = workflow.info()
        result = await workflow.execute_activity(
            route,
            RouteRequest(prompt=prompt, floor=floor, ceiling=ceiling),
            start_to_close_timeout=ROUTE_TIMEOUT,
            retry_policy=ROUTE_RETRY,
        )
        confidence = f", confidence {result.confidence:.2f}" if result.confidence is not None else ""
        workflow.logger.info(
            "%s: routed to tier %s (%s%s), candidates %s",
            info.workflow_id,
            result.tier,
            result.method,
            confidence,
            ", ".join(c.label for c in result.candidates),
        )
        return result

    async def _publish(
        self, prepared: PreparedRun, harness: HarnessResult, candidate: Candidate, spent: Decimal
    ) -> PublishResult:
        info = workflow.info()
        workflow.logger.info("%s: publishing run %s to %s", info.workflow_id, harness.run_id, prepared.repo)
        published = await workflow.execute_activity(
            publish,
            PublishRequest(
                run_id=harness.run_id,
                repo=prepared.repo,
                ticket=prepared.ticket,
                title=prepared.title,
                candidate=candidate.label,
                summary=harness.answer,
                spent_usd=str(spent),
            ),
            start_to_close_timeout=PUBLISH_TIMEOUT,
            retry_policy=PUBLISH_RETRY,
        )
        if published.pr_url:
            workflow.logger.info("%s: pull request %s", info.workflow_id, published.pr_url)
        return published

    async def _review_loop(self, loop: "_Loop") -> tuple[str, str]:
        """The review loop on the task's pull request. Returns its outcome and why."""
        info = workflow.info()
        log = workflow.logger
        try:
            policy = await workflow.execute_activity(
                review_policy,
                f"{loop.author.engine}:{loop.author.model}",
                start_to_close_timeout=RECORD_TIMEOUT,
                retry_policy=PREPARE_RETRY,
            )
            fixes = 0
            reviews = 0
            while True:
                checks = await workflow.execute_activity(
                    wait_checks,
                    ChecksRequest(repo=loop.prepared.repo, head_sha=loop.head, task_id=self._task_id),
                    start_to_close_timeout=CHECKS_TIMEOUT,
                    heartbeat_timeout=CHECKS_HEARTBEAT_TIMEOUT,
                    retry_policy=CODE_HOST_RETRY,
                )
                log.info("%s: checks at %s: %s", info.workflow_id, loop.head[:12], checks.state)
                if checks.state == "failed":
                    fix = loop.prompt_request("fix", failed_checks=checks.failed)
                else:
                    if not policy.reviewers:
                        why = "; ".join(policy.unresolved) or "every reviewer pair is the model that wrote it"
                        return "unreviewed", f"the pull request has no reviewer: {why}"
                    reviews += 1
                    review, reviewer, why = await self._review(loop, policy.reviewers, reviews)
                    if review is None:
                        return "review_unresolved", f"review round {reviews} got no verdict: {why}"
                    await workflow.execute_activity(
                        post_review,
                        PostReviewRequest(
                            task_id=self._task_id,
                            repo=loop.prepared.repo,
                            pr_url=loop.published.pr_url,
                            head_sha=loop.head,
                            reviewer=reviewer,
                            review=review,
                            round_no=reviews,
                        ),
                        start_to_close_timeout=CODE_HOST_TIMEOUT,
                        retry_policy=CODE_HOST_RETRY,
                    )
                    log.info("%s: review round %d by %s: %s", info.workflow_id, reviews, reviewer, review["verdict"])
                    if review["verdict"] == "clean":
                        clean = f"review round {reviews} by {reviewer} came back clean at {loop.head[:12]}"
                        merged = await self._merge(loop, clean)
                        if merged.state == "head_changed":
                            log.info("%s: a commit landed after the review: %s", info.workflow_id, merged.head_sha)
                            loop.head = merged.head_sha
                            continue
                        if merged.state != "conflict":
                            return ("merged" if merged.state == "merged" else "clean"), merged.detail
                        fix = loop.prompt_request("fix", conflict=True)
                    else:
                        fix = loop.prompt_request(
                            "fix",
                            findings=list(review.get("findings") or []),
                            summary=str(review.get("summary") or ""),
                        )
                if fixes >= policy.max_rounds:
                    return "review_unresolved", f"the review loop used its {policy.max_rounds} fix rounds"
                fixes += 1
                stopped = await self._fix(loop, fix, fixes)
                if stopped:
                    return "review_unresolved", stopped
        except ActivityError as err:
            if isinstance(err.cause, CancelledError):
                raise
            return "review_unresolved", f"the review loop stopped: {err.cause or err}"[:500]

    async def _review(self, loop: "_Loop", reviewers: list[Candidate], round_no: int) -> tuple[dict | None, str, str]:
        """One review round: each reviewer pair in order until one returns a verdict."""
        prompt = await self._loop_prompt(loop.prompt_request("review"))
        why = ""
        for reviewer in reviewers:
            if loop.left() <= 0:
                return None, "", f"the task spent its ${loop.limit} spend limit"
            harness, detail = await self._loop_run(loop, prompt, reviewer, "review", thinking=None)
            if harness is not None and harness.review:
                return harness.review, reviewer.label, ""
            why = f"{reviewer.label}: {detail}"
        return None, "", why

    async def _merge(self, loop: "_Loop", clean: str):
        """The merge after a clean review, when the goal allows it. Returns the merge's result, whose
        detail is the reason the task ends with."""
        auto = False
        if loop.request.goal_id:
            auto = await workflow.execute_activity(
                goal_auto_merge,
                loop.request.goal_id,
                start_to_close_timeout=TASK_API_TIMEOUT,
                retry_policy=TASK_API_RETRY,
            )
        if not auto:
            why = "its goal has auto_merge off" if loop.request.goal_id else "the task has no goal to allow a merge"
            return MergeResult(state="ready", detail=f"{clean}; {why}, so it waits for an operator")
        merged = await workflow.execute_activity(
            merge_pr,
            MergeRequest(
                task_id=self._task_id,
                repo=loop.prepared.repo,
                pr_url=loop.published.pr_url,
                branch=loop.published.branch,
                base=loop.published.base,
                head_sha=loop.head,
            ),
            start_to_close_timeout=CODE_HOST_TIMEOUT,
            retry_policy=CODE_HOST_RETRY,
        )
        workflow.logger.info("%s: merge at %s: %s", workflow.info().workflow_id, loop.head[:12], merged.state)
        if merged.state == "merged":
            merged.detail = f"{clean}; merged as {merged.merge_sha[:12]}"
        elif merged.state == "blocked":
            merged.detail = f"{clean}; GitHub would not merge it: {merged.detail}"
        return merged

    async def _fix(self, loop: "_Loop", fix: LoopPromptRequest, round_no: int) -> str:
        """One fix run on the pull request's branch, published to it. Returns why the loop must stop, or ""."""
        if loop.left() <= 0:
            return f"the task spent its ${loop.limit} spend limit before fix round {round_no}"
        fix.round_no = round_no
        prompt = await self._loop_prompt(fix)
        merge_base = loop.published.base if fix.conflict else ""
        harness, detail = await self._loop_run(
            loop, prompt, loop.author, "fix", thinking=loop.thinking, merge_base=merge_base
        )
        if harness is None or harness.status != "FINISHED":
            return f"fix round {round_no} on {loop.author.label} did not finish: {detail}"
        republished = await self._publish(loop.prepared, harness, loop.author, loop.spent)
        if not republished.pr_url or republished.commit == loop.head:
            return f"fix round {round_no} changed nothing"
        loop.head = republished.commit
        return ""

    async def _loop_run(
        self,
        loop: "_Loop",
        prompt: str,
        candidate: Candidate,
        kind: str,
        thinking: str | None,
        merge_base: str = "",
    ) -> tuple[HarnessResult | None, str]:
        """A review or fix run on the pull request's branch, recorded as the task's next attempt."""
        info = workflow.info()
        attempt = len(loop.attempts) + 1
        run_id = run_id_for(info.workflow_id, attempt)
        self._current_run_id = run_id
        harness, decision, detail, run_spent = await self._try(
            prompt,
            run_id,
            candidate,
            loop.left(),
            loop.prepared.repo,
            thinking,
            branch=loop.published.branch,
            merge_base=merge_base,
        )
        loop.spent += run_spent
        loop.attempts.append(
            Attempt(
                attempt=attempt,
                tier=kind,
                candidate=candidate.label,
                run_id=run_id,
                decision=decision,
                detail=detail,
                spent_usd=str(run_spent),
            )
        )
        if self._task_id:
            await self._record(record_attempt, AttemptRecord(self._task_id, loop.attempts[-1], candidate, harness))
        workflow.logger.info(
            "%s: %s run %d on %s: %s. Spent $%s of $%s",
            info.workflow_id,
            kind,
            attempt,
            candidate.label,
            detail,
            loop.spent,
            loop.limit,
        )
        return harness, detail

    async def _loop_prompt(self, request: LoopPromptRequest) -> str:
        return await workflow.execute_activity(
            loop_prompt, request, start_to_close_timeout=PREPARE_TIMEOUT, retry_policy=PREPARE_RETRY
        )

    async def _try(
        self,
        prompt: str,
        run_id: str,
        candidate: Candidate,
        left: Decimal,
        repo: str,
        thinking: str | None,
        branch: str = "",
        merge_base: str = "",
    ) -> tuple[HarnessResult | None, str, str, Decimal]:
        """One run on one candidate. Returns the result, the decision, why, and what the run spent."""
        try:
            harness = await workflow.execute_activity(
                run_harness,
                HarnessRequest(
                    run_id=run_id,
                    prompt=prompt,
                    candidate=candidate,
                    spend_limit_usd=str(left),
                    repo=repo,
                    task_id=self._task_id,
                    thinking=thinking,
                    branch=branch,
                    merge_base=merge_base,
                ),
                start_to_close_timeout=harness_timeout(candidate),
                heartbeat_timeout=HARNESS_HEARTBEAT_TIMEOUT,
                retry_policy=HARNESS_RETRY,
                # On cancel, wait until the activity has stopped pi before the workflow ends.
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
        except ActivityError as err:
            if isinstance(err.cause, CancelledError):
                workflow.logger.info("%s: canceled, and run_harness has stopped pi", workflow.info().workflow_id)
                raise
            kind = err.cause.type if isinstance(err.cause, ApplicationError) else type(err.cause).__name__
            cleanup = await workflow.execute_activity(
                cleanup_run, run_id, start_to_close_timeout=CLEANUP_TIMEOUT, retry_policy=PREPARE_RETRY
            )
            run_spent = Decimal(cleanup.grant_spent_usd or 0)
            detail = f"run_harness failed ({kind}): {err.cause}"
            if kind in NEXT_CANDIDATE_ERROR_TYPES or isinstance(err.cause, TimeoutError):
                return None, "next_candidate", detail, run_spent
            return None, "final", detail, run_spent
        run_spent = Decimal(harness.grant_spent_usd or 0)
        detail = f"ended {harness.status} ({harness.end_reason})"
        if harness.status == "FINISHED":
            return harness, "finished", detail, run_spent
        if harness.end_reason in NEXT_CANDIDATE_END_REASONS:
            return harness, "next_candidate", f"{detail}: {harness.error}", run_spent
        return harness, "final", f"{detail}: {harness.error}", run_spent

    @staticmethod
    def _escalation(route_result: RouteResult | None, request: RunTaskInput) -> str | None:
        """The tier one above the one just used up, when the router escalates and the ceiling allows it."""
        if route_result is None or route_result.on_exhausted != "escalate":
            return None
        index = route_result.tiers.index(route_result.tier)
        if index + 1 >= len(route_result.tiers):
            return None
        if request.ceiling and route_result.tiers.index(request.ceiling) <= index:
            return None
        return route_result.tiers[index + 1]


class _Loop:
    """What the review loop carries between its steps: the pull request, its head, and the task's spend."""

    def __init__(
        self,
        request: RunTaskInput,
        prepared: PreparedRun,
        author: Candidate,
        published: PublishResult,
        limit: Decimal,
        spent: Decimal,
        attempts: list[Attempt],
        thinking: str | None,
    ) -> None:
        self.request = request
        self.prepared = prepared
        self.author = author
        self.published = published
        self.head = published.commit
        self.limit = limit
        self.spent = spent
        self.attempts = attempts
        self.thinking = thinking

    def left(self) -> Decimal:
        return self.limit - self.spent

    def prompt_request(self, kind: str, **fields) -> LoopPromptRequest:
        return LoopPromptRequest(
            kind=kind,
            repo=self.prepared.repo,
            title=self.prepared.title,
            description=self.prepared.description,
            checks=list(self.prepared.checks),
            base=self.published.base,
            branch=self.published.branch,
            **fields,
        )


def harness_timeout(candidate: Candidate) -> timedelta:
    """How long one run_harness attempt may take: the profile's max_minutes and the grace for start and Collect."""
    minutes = float(candidate.profile.get("max_minutes") or DEFAULT_RUN_MINUTES)
    return timedelta(minutes=minutes) + HARNESS_GRACE


def _hints(request: RunTaskInput) -> str:
    hints = [f"floor {request.floor}" if request.floor else "", f"ceiling {request.ceiling}" if request.ceiling else ""]
    return "routed by the ai-api-unified router" + "".join(f", {hint}" for hint in hints if hint)


def _failed(message: str, attempts: list[Attempt]) -> ApplicationError:
    """The task's failure, with every run it tried, so the starter can print them."""
    return ApplicationError(message, [vars(a) for a in attempts], type="TaskFailed", non_retryable=True)
