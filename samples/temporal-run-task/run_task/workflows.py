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
pushes it and opens the pull request, and the task completes with its URL. A
finished run that changed nothing fails the task.

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

from datetime import timedelta
from decimal import Decimal

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError, TimeoutError

with workflow.unsafe.imports_passed_through():
    from run_task.activities import cleanup_run, prepare, publish, route, run_harness
    from run_task.shared import (
        Attempt,
        Candidate,
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

PREPARE_TIMEOUT = timedelta(seconds=30)
PREPARE_RETRY = RetryPolicy(maximum_attempts=3)
ROUTE_TIMEOUT = timedelta(seconds=60)
ROUTE_RETRY = RetryPolicy(maximum_attempts=3, non_retryable_error_types=["HarnessConfigError"])
CLEANUP_TIMEOUT = timedelta(seconds=60)
PUBLISH_TIMEOUT = timedelta(minutes=2)
PUBLISH_RETRY = RetryPolicy(initial_interval=timedelta(seconds=5), maximum_attempts=3)
# The whole pi run, from start to the [run] line.
HARNESS_TIMEOUT = timedelta(minutes=10)
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


@workflow.defn
class RunTask:
    def __init__(self) -> None:
        self._current_run_id: str | None = None

    @workflow.query
    def current_run_id(self) -> str | None:
        """The run the task is on now, so `make watch` and `make status` find its per-run sandbox."""
        return self._current_run_id

    @workflow.run
    async def run(self, request: RunTaskInput) -> RunTaskResult:
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
            for candidate in candidates:
                left = limit - spent
                if left <= 0:
                    raise _failed(f"the task spent its ${limit} spend limit after {len(attempts)} runs", attempts)
                attempt = len(attempts) + 1
                run_id = run_id_for(info.workflow_id, attempt)
                self._current_run_id = run_id
                harness, decision, detail, run_spent = await self._try(
                    prepared.prompt, run_id, candidate, left, prepared.repo
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
                    log.info("%s: completed", info.workflow_id)
                    return RunTaskResult(
                        template=prepared.template,
                        prompt=prepared.prompt,
                        route=route_result,
                        attempts=attempts,
                        harness=harness,
                        spent_usd=str(spent),
                        published=published,
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

    async def _try(
        self, prompt: str, run_id: str, candidate: Candidate, left: Decimal, repo: str
    ) -> tuple[HarnessResult | None, str, str, Decimal]:
        """One run on one candidate. Returns the result, the decision, why, and what the run spent."""
        try:
            harness = await workflow.execute_activity(
                run_harness,
                HarnessRequest(run_id=run_id, prompt=prompt, candidate=candidate, spend_limit_usd=str(left), repo=repo),
                start_to_close_timeout=HARNESS_TIMEOUT,
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


def _hints(request: RunTaskInput) -> str:
    hints = [f"floor {request.floor}" if request.floor else "", f"ceiling {request.ceiling}" if request.ceiling else ""]
    return "routed by the ai-api-unified router" + "".join(f", {hint}" for hint in hints if hint)


def _failed(message: str, attempts: list[Attempt]) -> ApplicationError:
    """The task's failure, with every run it tried, so the starter can print them."""
    return ApplicationError(message, [vars(a) for a in attempts], type="TaskFailed", non_retryable=True)
