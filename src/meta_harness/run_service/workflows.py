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
   after the review goes back to step 1, so it is checked and reviewed too, and
   so does a branch merge_pr brought the base's newest commits into: merges
   are one at a time per repo, each on a head that holds the base. A conflict
   with the base goes to a fix run that resolves the merge the host started,
   and back to step 1.

The loop ends review_unresolved after max_rounds fix runs, when the task's
spend limit is used up, when a fix run changes nothing, or when no reviewer
pair returns a verdict. A merged pull request ends the task closed. A clean
one that does not merge (auto_merge off, no goal, or GitHub refusing) ends it
ready, with the reason.

Before each run of a Task API task, record_prompt records the run's filled-in
prompt as run_prompt (behind the run-prompt patch), so an operator can tell a
bad prompt from a bad model.

The task's spend limit covers all its runs: each run's grant gets what the
earlier runs left, or the task's share of what its goal has left when that is
less. A task with too little for its next run holds for an operator, who
raises the limit or ends it (budget_activities.py). A run run_harness gave up on still counts, from what
cleanup_run reads off its grant.

Workflow code runs in the SDK's sandbox and must be deterministic: no I/O, no
clocks, no subprocesses. All of that lives in the activities. The activity and
payload modules are passed through the sandbox so it does not reload them.

workflow.logger skips its output while the worker replays history, so each
step logs once, when it first runs. A worker that picks the workflow up from
history prints one line saying it replayed.
"""

import asyncio
import json
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
        base_health,
        post_comment,
        wait_checks,
        wait_merge_checks,
    )
    from meta_harness.run_service.budget_activities import BudgetHold, budget_hold, goal_budget
    from meta_harness.run_service.prompt_activities import RunPromptRecord, loop_template, record_prompt
    from meta_harness.run_service.resume_activities import resume_state
    from meta_harness.money import usd
    from meta_harness.run_service.shared import (
        Attempt,
        BaseHealthRequest,
        CommentRequest,
        DeliverRequest,
        OperatorMessage,
        ChecksRequest,
        LoopPromptRequest,
        MergeChecksRequest,
        ref_for,
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
# The failure a run activity ends with when its worker shuts down, how many times one run is taken up again
# after it, and how long the workflow waits for the worker to be back before it asks.
WORKER_SHUTDOWN = "WorkerShutdown"
WORKER_RESTARTS = 5
WORKER_RESTART_WAIT = timedelta(seconds=20)
# run_finished end reasons the next candidate may get past: the infrastructure failed, not the task.
NEXT_CANDIDATE_END_REASONS = frozenset({"model_error", "harness_error", "server_shutdown"})
# run_harness failures the next candidate may get past. A failed clone (CodeHostError) is the repo's, not the candidate's.
NEXT_CANDIDATE_ERROR_TYPES = frozenset({"SandboxUnavailable", "HarnessUnavailable", "GatewayUnavailable"})
# The review loop's steps. A repo's checks may take a while; wait_checks heartbeats while it polls.
CHECKS_TIMEOUT = timedelta(minutes=75)
CHECKS_HEARTBEAT_TIMEOUT = timedelta(minutes=2)
# Longer than wait_merge_checks's own 120 minutes, which ends the wait with a result.
MERGE_CHECKS_TIMEOUT = timedelta(minutes=125)
# How often a merge waiting for the base's own checks looks again.
BASE_HEALTH_POLL_MINUTES = 1
# How long a task held for budget waits for an operator before it ends as it would have. It holds its repo-area
# lock and its place in the parallel limit while it waits.
BUDGET_HOLD = timedelta(hours=24)
# How often a task held for its goal's budget looks again, so raising the goal's budget lets its tasks go on.
BUDGET_RECHECK = timedelta(minutes=5)
# The least a run is started with. A run given less ends on its spend limit before it can do anything.
MIN_RUN_USD = Decimal("0.10")
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
        # The attempt an operator picked, when the task made several at once.
        self._chosen: int | None = None
        # An operator's answer to a budget hold: "raise", "raise:<new spend limit>" or "end".
        self._budget_answer: str | None = None
        # Set once a budget hold ended with no more budget: the task then ends, and never holds again.
        self._budget_ended = False
        self._task_type = ""
        # An operator's words on a review held for them, as they came: "dismiss:<round>:<n>:<why>", "proceed:<round>".
        self._review_words: list[str] = []
        # The operator who took the review over ("" when no one has), and their verdict once they give it, as
        # JSON: {"keep": [the agent's findings they keep, from 1], "findings": [their own]}.
        self._review_claim = ""
        self._operator_verdict: str | None = None

    @workflow.signal
    def operator_message(self, message: OperatorMessage) -> None:
        """An operator's message for the run in progress, or stop, or the pick among parallel attempts. The scheduler
        sends it from the task service."""
        if message.when == "choose":
            self._chosen = int(message.text)
            return
        if message.when == "budget":
            self._budget_answer = message.text
            return
        if message.when == "review":
            # An operator review: "claim:<who>", "release", "verdict:<json>". Any other word is for a held review.
            kind, _, rest = message.text.partition(":")
            if kind == "claim":
                self._review_claim = rest
            elif kind == "release":
                self._review_claim = ""
            elif kind == "verdict":
                self._operator_verdict = rest
            else:
                self._review_words.append(message.text)
            return
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
            attempts=spec.attempts,
            resume=spec.resume,
            task_type=spec.task_type,
        )
        self._task_type = spec.task_type
        again = f" again (retry {spec.retries})" if spec.retries else ""
        await self._task_status("running", f"the meta-harness run service started its RunTask workflow{again}")
        try:
            result = await self._run_loop(request)
        except asyncio.CancelledError:
            await self._task_status("canceled", "the RunTask workflow was canceled")
            raise
        except ActivityError as err:
            if isinstance(err.cause, CancelledError):
                await self._task_status("canceled", "the RunTask workflow was canceled")
            else:
                workflow.logger.error("%s failed: %s", self._where(), err.cause or err)
                await self._task_status("failed", str(err.cause or err)[:500])
            raise
        except ApplicationError as err:
            workflow.logger.error("%s failed: %s", self._where(), err)
            await self._task_status("failed", str(err)[:500])
            raise
        pr_url = result.published.pr_url if result.published else ""
        if result.outcome == "review_unresolved":
            workflow.logger.warning("%s ended review_unresolved: %s", self._where(), result.outcome_reason)
            await self._task_status("review_unresolved", result.outcome_reason, pr_url)
        elif result.outcome in ("merged", "planned"):
            await self._task_status("closed", result.outcome_reason, pr_url)
        elif result.outcome == "merge_checks_failed":
            workflow.logger.error("%s failed: %s", self._where(), result.outcome_reason)
            await self._task_status("failed", result.outcome_reason[:500], pr_url)
        elif pr_url:
            if result.outcome == "unreviewed" or "GitHub would not merge it" in result.outcome_reason:
                workflow.logger.warning("%s ended %s: %s", self._where(), result.outcome, result.outcome_reason)
            await self._task_status("ready", result.outcome_reason or "the run finished", pr_url)
        else:
            await self._task_status("closed", "the run finished")
        return result

    def _holds(self) -> bool:
        """Whether this task holds for budget rather than ending when it runs out. Behind a patch: a workflow
        from before it ends, as it always did."""
        # A deploy's smoke task never holds: nobody is there to answer, and the deploy is waiting.
        if self._task_type == "smoke":
            return False
        return bool(self._task_id) and not self._budget_ended and workflow.patched("budget-hold")

    async def _grant(
        self, goal_id: str, limit: Decimal, spent: Decimal, need: Decimal, what: str
    ) -> tuple[Decimal | None, Decimal]:
        """What the next run may spend, and the task's spend limit, which an operator may have raised.

        The run gets what the task has left, or the task's share of what its goal has left when that is less.
        When that is under what the run needs (need: what the last run of its kind cost), the task holds:
        it stays running, marked as waiting on an operator for budget, until the operator raises the limit or
        ends it, or BUDGET_HOLD passes. A task held for its goal's budget looks again every BUDGET_RECHECK,
        so raising the goal's budget lets it go on. None: the task must end, out of budget.
        """
        if not self._holds():
            left = limit - spent
            return (left if left > 0 else None), limit
        need = max(need, MIN_RUN_USD)
        waited = timedelta(0)
        held = False
        while True:
            left = limit - spent
            why = "" if left >= need else f"the task has {usd(left)} left of its {usd(limit)} spend limit"
            share = None
            if goal_id:
                goal = await workflow.execute_activity(
                    goal_budget, goal_id, start_to_close_timeout=TASK_API_TIMEOUT, retry_policy=TASK_API_RETRY
                )
                share = goal.share()
                if not why and share is not None and share < need:
                    why = (
                        f"goal {goal_id} has spent {usd(goal.spent_usd)} of its {usd(goal.max_usd)} budget, which "
                        f"leaves {usd(share)} for each of its {goal.running} running tasks"
                    )
            if not why:
                if held:
                    await self._budget_hold(False, f"the budget was raised; carrying on with {what}")
                return (left if share is None else min(left, share)), limit
            for_goal = why.startswith("goal ")
            if not held:
                held = True
                self._budget_answer = None
                raise_what = "the goal's budget" if for_goal else "the task's spend limit"
                await self._budget_hold(
                    True, f"{what} needs about {usd(need)}, and {why}. Raise {raise_what}, or end the task"
                )
                workflow.logger.warning("%s is held for budget before %s: %s", self._where(), what, why)
            wait = min(BUDGET_RECHECK, BUDGET_HOLD - waited) if for_goal else BUDGET_HOLD - waited
            try:
                await workflow.wait_condition(lambda: self._budget_answer is not None, timeout=wait)
            except asyncio.TimeoutError:
                waited += wait
            answer, self._budget_answer = self._budget_answer, None
            if answer == "end" or (answer is None and waited >= BUDGET_HOLD):
                ended = "an operator ended it" if answer else f"no one raised the budget in {BUDGET_HOLD}"
                await self._budget_hold(False, f"{why}, and {ended}")
                self._budget_ended = True
                return None, limit
            if answer and answer.startswith("raise:"):
                limit = Decimal(answer.partition(":")[2])

    async def _budget_hold(self, waiting: bool, reason: str) -> None:
        await self._hold(waiting, reason, "budget")

    async def _hold(self, waiting: bool, reason: str, on: str) -> None:
        """Marks the running task as waiting on an operator (for budget, or for its review), or as waiting no
        longer, with why."""
        await workflow.execute_activity(
            budget_hold,
            BudgetHold(task_id=self._task_id, waiting=waiting, reason=reason, on=on),
            start_to_close_timeout=TASK_API_TIMEOUT,
            retry_policy=TASK_API_RETRY,
        )

    def _where(self, run_id: str = "") -> str:
        """How a warning or error names its task and run, so a search for either id finds every line about it."""
        task = self._task_id or workflow.info().workflow_id
        run = run_id or self._current_run_id
        return f"task {task} run {run}" if run else f"task {task}"

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
        if request.resume and self._task_id:
            # An operator sent the task back: pick up at its open pull request, or close it if that merged.
            # Otherwise a new first run follows, its runs numbered after the earlier ones.
            resumed, attempts = await self._resume(request, prepared, limit)
            if resumed is not None:
                return resumed
        route_result: RouteResult | None = None
        floor = request.floor
        # Behind a patch: only a task asked for several attempts takes this path, and only after the patch.
        if request.attempts > 1 and not prepared.pinned and workflow.patched("parallel-attempts"):
            return await self._parallel_attempts(request, prepared, limit)
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
            pending = list(candidates)
            while pending:
                candidate = pending.pop(0)
                left, limit = await self._grant(
                    request.goal_id, limit, spent, _need(attempts, "task"), f"run {len(attempts) + 1}"
                )
                if left is None:
                    raise _failed(f"the task spent its {usd(limit)} spend limit after {len(attempts)} runs", attempts)
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
                    kind="task",
                    template=prepared.template,
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
                if decision != "finished":
                    log.warning(
                        "%s: run %d (%s tier) on %s did not finish: %s",
                        self._where(run_id),
                        attempt,
                        tier,
                        candidate.label,
                        detail,
                    )
                if decision == "finished" and harness is not None:
                    return await self._finish(
                        request, prepared, route_result, candidate, harness, attempt, attempts, limit, spent
                    )
                if decision == "final" and _out_of_budget(harness) and self._holds():
                    # The run stopped at its spend limit with its work unfinished: ask for more, on the same
                    # candidate, before giving the task up.
                    pending.insert(0, candidate)
                    continue
                if decision == "final":
                    raise _failed(f"run {attempt} on {candidate.label} ended for good: {detail}", attempts)
            next_tier = self._escalation(route_result, request) if not prepared.pinned else None
            if next_tier is None:
                raise _failed(f"every candidate failed ({len(attempts)} runs)", attempts)
            log.info(
                "%s: tier %s has no candidates left, so the task escalates to %s", info.workflow_id, tier, next_tier
            )
            floor = next_tier

    async def _finish(
        self,
        request: RunTaskInput,
        prepared: PreparedRun,
        route_result: RouteResult | None,
        candidate: Candidate,
        harness: HarnessResult,
        attempt: int,
        attempts: list[Attempt],
        limit: Decimal,
        spent: Decimal,
    ) -> RunTaskResult:
        """A finished run's end: publish its changes, then the review loop on its pull request."""
        info = workflow.info()
        # A planning task proposes issues and changes no file: nothing to publish, no pull request, no review.
        # Behind a patch, as every change to what a workflow runs is.
        # A deploy's smoke task only shows that a task still runs: its run finished, and that is the result.
        if request.task_type == "smoke" and workflow.patched("smoke-task"):
            workflow.logger.info("%s: the smoke run finished", info.workflow_id)
            return RunTaskResult(
                template=prepared.template,
                prompt=prepared.prompt,
                route=route_result,
                attempts=attempts,
                harness=harness,
                spent_usd=str(spent),
                outcome="planned",
                outcome_reason=f"the smoke run finished in {harness.turns} turns for {usd(spent)}",
            )
        if request.task_type == "plan" and workflow.patched("plan-task"):
            workflow.logger.info("%s: the planning run finished; the planner reads its drafts", info.workflow_id)
            return RunTaskResult(
                template=prepared.template,
                prompt=prepared.prompt,
                route=route_result,
                attempts=attempts,
                harness=harness,
                spent_usd=str(spent),
                outcome="planned",
                outcome_reason="the planning run finished, and the planner reads its drafts into the goal's plan",
            )
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
            # Behind a patch: workflows started before it end closed at the merge, and replay as they ran.
            if outcome == "merged" and loop.merge_sha and workflow.patched("merge-checks"):
                outcome, reason = await self._after_merge(loop, reason)
                published = loop.published
            spent = loop.spent
        workflow.logger.info(
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

    async def _parallel_attempts(self, request: RunTaskInput, prepared: PreparedRun, limit: Decimal) -> RunTaskResult:
        """Several attempts at once, each on one of the tier's candidates with an equal share of the spend limit,
        each in its own sandbox and workspace. The finished ones wait for an operator to pick one, whose changes
        publish as the pull request; the others are never pushed."""
        info = workflow.info()
        route_result = await self._route(prepared.prompt, request.floor, request.ceiling)
        if self._task_id:
            self._route_no += 1
            await self._record(
                record_route, RouteRecord(self._task_id, self._route_no, request.floor, request.ceiling, route_result)
            )
        picked = route_result.candidates[: request.attempts]
        share = (limit / len(picked)).quantize(Decimal("0.0001"))
        runs = await asyncio.gather(
            *(
                self._try(
                    prepared.prompt,
                    run_id_for(info.workflow_id, number),
                    candidate,
                    share,
                    prepared.repo,
                    route_result.thinking,
                    kind="task",
                    template=prepared.template,
                )
                for number, candidate in enumerate(picked, start=1)
            )
        )
        attempts: list[Attempt] = []
        spent = Decimal(0)
        finished: dict[int, tuple[Candidate, HarnessResult]] = {}
        for number, (candidate, (harness, decision, detail, run_spent)) in enumerate(zip(picked, runs), start=1):
            spent += run_spent
            attempts.append(
                Attempt(
                    attempt=number,
                    tier=route_result.tier,
                    candidate=candidate.label,
                    run_id=run_id_for(info.workflow_id, number),
                    decision=decision,
                    detail=detail,
                    spent_usd=str(run_spent),
                )
            )
            if self._task_id:
                await self._record(record_attempt, AttemptRecord(self._task_id, attempts[-1], candidate, harness))
            if decision == "finished" and harness is not None:
                finished[number] = (candidate, harness)
        if not finished:
            raise _failed(f"none of the {len(picked)} attempts finished", attempts)
        if len(finished) == 1:
            number = next(iter(finished))
        else:
            if self._task_id:
                await self._task_status(
                    "running",
                    f"{len(finished)} attempts finished: waiting for an operator to compare them and pick one",
                )
            await workflow.wait_condition(lambda: self._chosen in finished)
            number = self._chosen
        workflow.logger.info("%s: attempt %s picked of %s", info.workflow_id, number, sorted(finished))
        candidate, harness = finished[number]
        self._current_run_id = harness.run_id
        return await self._finish(request, prepared, route_result, candidate, harness, number, attempts, limit, spent)

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
                task_id=self._task_id,
            ),
            start_to_close_timeout=PUBLISH_TIMEOUT,
            retry_policy=PUBLISH_RETRY,
        )
        if published.pr_url:
            workflow.logger.info("%s: pull request %s", info.workflow_id, published.pr_url)
        return published

    async def _resume(
        self, request: RunTaskInput, prepared: PreparedRun, limit: Decimal
    ) -> tuple[RunTaskResult | None, list[Attempt]]:
        """Picks a retried task up at its pull request. Returns the result, or None with the earlier runs when
        a new first run must be made: the task has no pull request, it was closed unmerged, or the pair that
        wrote it can no longer be resolved.

        An open pull request goes back through the review loop with the task's fix rounds counted afresh, or,
        when an operator asked for the merge, is merged whatever its goal's auto_merge says. One that has
        merged since ends the task merged.
        """
        info = workflow.info()
        state = await workflow.execute_activity(
            resume_state, self._task_id, start_to_close_timeout=CODE_HOST_TIMEOUT, retry_policy=CODE_HOST_RETRY
        )
        attempts = list(state.attempts)

        def result(outcome: str, reason: str, published: PublishResult | None, spent: Decimal) -> RunTaskResult:
            workflow.logger.info("%s: resumed (%s), %s: %s", info.workflow_id, request.resume, outcome, reason)
            return RunTaskResult(
                template=prepared.template,
                prompt=prepared.prompt,
                route=None,
                attempts=attempts,
                harness=None,
                spent_usd=str(spent),
                published=published,
                outcome=outcome,
                outcome_reason=reason,
            )

        if state.pr_state == "merged":
            merged = f"its pull request {state.published.pr_url} had merged"
            return result("merged", merged, state.published, Decimal(0)), attempts
        if state.pr_state != "open" or state.published is None or state.author is None:
            return None, attempts
        loop = _Loop(request, prepared, state.author, state.published, limit, Decimal(0), attempts, None)
        await self._task_status("running", "its pull request is open, back in the review loop", loop.published.pr_url)
        if request.resume == "merge":
            asked = f"an operator asked for the merge at {loop.head[:12]}"
            merged = await self._merge(loop, asked, asked_for=True)
            if merged.state == "merged":
                loop.merge_sha = merged.merge_sha
                outcome, reason = "merged", merged.detail
            elif merged.state == "blocked":
                outcome, reason = "clean", merged.detail
            else:
                # The head moved, or it conflicts with its base: checks, review and fixes come first.
                loop.head = merged.head_sha or loop.head
                outcome, reason = await self._review_loop(loop)
        else:
            outcome, reason = await self._review_loop(loop)
        if outcome == "merged" and loop.merge_sha:
            outcome, reason = await self._after_merge(loop, reason)
        return result(outcome, reason, loop.published, loop.spent), attempts

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
            loop.max_rounds = policy.max_rounds
            loop.merge_wait_minutes = policy.merge_wait_minutes
            fixes = 0
            reviews = 0
            # A clean verdict that still stands at the new head: the base was brought in and the pull request's
            # own diff is what the reviewer read. Its checks run again, and it merges with no new review.
            standing = ""
            # The round before's findings and summary, which the next review judges first.
            previous: dict = {}
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
                    standing = ""
                    fix = loop.prompt_request("fix", failed_checks=checks.failed)
                elif standing:
                    clean, standing = f"{standing}, and its checks passed at {loop.head[:12]}", ""
                    merged = await self._merge(loop, clean)
                    if merged.state == "head_changed":
                        loop.head = merged.head_sha
                        standing = self._still_clean(merged, clean, loop)
                        continue
                    if merged.state != "conflict":
                        loop.merge_sha = merged.merge_sha if merged.state == "merged" else ""
                        return ("merged" if merged.state == "merged" else "clean"), merged.detail
                    fix = loop.prompt_request("fix", conflict=True)
                else:
                    if not policy.reviewers:
                        why = "; ".join(policy.unresolved) or "every reviewer pair is the model that wrote it"
                        return "unreviewed", f"the pull request has no reviewer: {why}"
                    reviews += 1
                    review, reviewer, why = await self._review(
                        loop, policy.reviewers, reviews, previous, list(checks.passed)
                    )
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
                    findings = list(review.get("findings") or [])
                    clean = f"review round {reviews} by {reviewer} came back clean at {loop.head[:12]}"
                    all_dismissed = False
                    # An operator who took the review over decides the round: the fix run gets the agent's
                    # findings they kept and their own, and with none the round is clean.
                    theirs = await self._operator_review(loop, reviews, findings)
                    if theirs is not None:
                        who, findings = theirs
                        all_dismissed = not findings
                        clean = f"{who} reviewed round {reviews} and asked for no change at {loop.head[:12]}"
                        review = {**review, "verdict": "changes" if findings else "clean"}
                    elif review["verdict"] != "clean" and findings and policy.hold_minutes > 0:
                        findings = await self._review_hold(loop, reviews, findings, policy.hold_minutes)
                        all_dismissed = not findings
                        if all_dismissed:
                            clean = f"an operator dismissed every finding of review round {reviews} at {loop.head[:12]}"
                    if review["verdict"] == "clean" or all_dismissed:
                        previous = {}
                        merged = await self._merge(loop, clean)
                        if merged.state == "head_changed":
                            log.info("%s: a commit landed after the review: %s", info.workflow_id, merged.head_sha)
                            loop.head = merged.head_sha
                            standing = self._still_clean(merged, clean, loop)
                            continue
                        if merged.state != "conflict":
                            loop.merge_sha = merged.merge_sha if merged.state == "merged" else ""
                            return ("merged" if merged.state == "merged" else "clean"), merged.detail
                        fix = loop.prompt_request("fix", conflict=True)
                    else:
                        previous = {**review, "findings": findings}
                        fix = loop.prompt_request(
                            "fix",
                            findings=findings,
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

    async def _after_merge(self, loop: "_Loop", reason: str) -> tuple[str, str]:
        """After a merge: wait for the merge commit's checks, deploys included. A failure becomes a fix run on a new
        branch from the base, whose pull request goes through the review loop, the merge and this wait again,
        within the task policy's fix rounds and the task's spend limit. Returns the outcome and why."""
        info = workflow.info()
        rounds = 0
        # Behind a patch: workflows from before it say nothing while they wait, and fix any failure.
        told = workflow.patched("merge-checks-judged")
        rechecked = False
        while True:
            if told and self._task_id:
                waiting = f"{reason}; waiting for the checks on the merge commit {loop.merge_sha[:12]}"
                await self._task_status("running", waiting, loop.published.pr_url)
            checks = await workflow.execute_activity(
                wait_merge_checks,
                MergeChecksRequest(
                    repo=loop.prepared.repo,
                    merge_sha=loop.merge_sha,
                    base=loop.published.base,
                    task_id=self._task_id,
                    round_no=rounds,
                ),
                start_to_close_timeout=MERGE_CHECKS_TIMEOUT,
                heartbeat_timeout=CHECKS_HEARTBEAT_TIMEOUT,
                retry_policy=CODE_HOST_RETRY,
            )
            names = ", ".join(f.name for f in checks.failed)
            workflow.logger.info("%s: checks on merge %s: %s", info.workflow_id, loop.merge_sha[:12], checks.state)
            if checks.state in ("failed", "pending"):
                workflow.logger.warning(
                    "%s: checks on the merge commit %s %s: %s",
                    self._where(),
                    loop.merge_sha[:12],
                    "failed" if checks.state == "failed" else "did not finish",
                    names,
                )
            if checks.state in ("passed", "none"):
                found = "passed" if checks.state == "passed" else "none ran"
                return "merged", f"{reason}; the merge commit's checks: {found}"
            if checks.state == "pending":
                return (
                    "merge_checks_failed",
                    f"{reason}; the merge commit's checks did not finish in 120 minutes: {names}",
                )
            merged_pr = loop.published.pr_url
            if rechecked:
                return (
                    "merge_checks_failed",
                    f"{reason}; {names} failed on the merge commit, and its fix changed nothing",
                )
            if told and not any(f.summary.strip() for f in checks.failed):
                # A fix run changes code, and a check that says nothing gives it nothing to change: a deploy
                # that failed with no output is for an operator, with the check's run on the code host.
                return "merge_checks_failed", (
                    f"{reason}; {names} failed on the merge commit {loop.merge_sha[:12]} with no output a fix run "
                    "could act on, so no fix run started: see the check's run on the code host"
                )
            if rounds >= loop.max_rounds:
                return "merge_checks_failed", (
                    f"{reason}; {names} failed on the merge commit, and the {loop.max_rounds} fix rounds are used up"
                )
            if loop.left() <= 0 and not self._holds():
                return (
                    "merge_checks_failed",
                    f"{reason}; {names} failed on the merge commit, and the spend limit is used up",
                )
            rounds += 1
            fix = loop.prompt_request("fix", failed_checks=checks.failed, merged_pr=merged_pr, round_no=rounds)
            fix.branch = loop.published.base
            if told and self._task_id:
                fixing = f"{reason}; fixing {names}, which failed on the merge commit {loop.merge_sha[:12]}"
                await self._task_status("running", fixing, merged_pr)
            prompt = await self._loop_prompt(fix)
            harness, detail = await self._loop_run(loop, prompt, loop.author, "fix", loop.thinking, branch="")
            if harness is None or harness.status != "FINISHED":
                return (
                    "merge_checks_failed",
                    f"{reason}; {names} failed on the merge commit, and its fix did not finish: {detail}",
                )
            published = await workflow.execute_activity(
                publish,
                PublishRequest(
                    run_id=harness.run_id,
                    repo=loop.prepared.repo,
                    ticket="",
                    title=f"Fix {names} after #{merged_pr.rsplit('/', 1)[-1]}",
                    candidate=loop.author.label,
                    summary=harness.answer,
                    spent_usd=str(loop.spent),
                    branch=f"mh/{ref_for(info.workflow_id)}-merge-{rounds}",
                    note=(
                        f"{merged_pr} merged, and then {names} failed on its merge commit {loop.merge_sha[:12]}. "
                        "This pull request fixes that. It closes no issue: the merged one closed it."
                    ),
                    task_id=self._task_id,
                ),
                start_to_close_timeout=PUBLISH_TIMEOUT,
                retry_policy=PUBLISH_RETRY,
            )
            if not published.pr_url:
                if told:
                    # Nothing to change may mean nothing was wrong with the code: look once more, since a newer
                    # commit's passing run of the same check settles it.
                    rechecked = True
                    continue
                return (
                    "merge_checks_failed",
                    f"{reason}; {names} failed on the merge commit, and its fix changed nothing",
                )
            if self._task_id:
                await self._record(record_published, PublishedRecord(self._task_id, published))
            loop.published = published
            loop.head = published.commit
            loop.merge_sha = ""
            loop.merge_fix = True
            outcome, reason = await self._review_loop(loop)
            if outcome != "merged" or not loop.merge_sha:
                if outcome == "merged":
                    outcome = "merge_checks_failed"
                return outcome, f"the fix for {names} after {merged_pr}: {reason}"

    async def _review_hold(self, loop: "_Loop", round_no: int, findings: list[dict], minutes: int) -> list[dict]:
        """Holds a review that asks for changes for an operator, before its fix run. Returns the findings the
        fix run is asked for: all of them, less those the operator dismissed.

        A wrong finding costs a fix round and can undo right work. So when the task policy sets
        review.hold_minutes, the task waits that long, marked as waiting on its review. The operator may
        dismiss findings, each with why, and may say go on now. The dismissals are posted on the pull
        request. Behind a patch: a workflow from before it sends every finding straight to the fix run.
        """
        if not self._task_id or not findings or not workflow.patched("review-hold"):
            return findings
        self._review_words = []
        count = f"{len(findings)} {'finding' if len(findings) == 1 else 'findings'}"
        waiting = (
            f"review round {round_no} asked for changes ({count}); waiting {minutes} minutes for an operator "
            "to dismiss any that are wrong before the fix run"
        )
        await self._hold(True, waiting, "review")
        dismissed: dict[int, str] = {}
        left = timedelta(minutes=minutes)
        started = workflow.now()
        proceed = False
        while not proceed and left > timedelta(0):
            try:
                await workflow.wait_condition(lambda: bool(self._review_words), timeout=left)
            except asyncio.TimeoutError:
                break
            words, self._review_words = self._review_words, []
            for word in words:
                kind, _, rest = word.partition(":")
                number, _, rest = rest.partition(":")
                if not number.isdigit() or int(number) != round_no:
                    continue
                if kind == "proceed":
                    proceed = True
                elif kind == "dismiss":
                    finding, _, why = rest.partition(":")
                    if finding.isdigit() and 1 <= int(finding) <= len(findings):
                        dismissed[int(finding)] = why
            left = timedelta(minutes=minutes) - (workflow.now() - started)
        kept = [f for n, f in enumerate(findings, 1) if n not in dismissed]
        if dismissed:
            lines = [f"**meta-harness review, round {round_no}**: an operator dismissed {len(dismissed)} of {count}."]
            for n in sorted(dismissed):
                where = findings[n - 1].get("path") or "the change"
                lines.append(f"- Finding {n} ({where}): dismissed. {dismissed[n]}")
            await workflow.execute_activity(
                post_comment,
                CommentRequest(
                    repo=loop.prepared.repo,
                    pr_url=loop.published.pr_url,
                    body="\n".join(lines),
                    task_id=self._task_id,
                ),
                start_to_close_timeout=CODE_HOST_TIMEOUT,
                retry_policy=CODE_HOST_RETRY,
            )
        after = (
            f"{len(kept)} of {count} go to the fix run"
            if kept
            else "every finding was dismissed, so the round counts as clean"
        )
        await self._hold(False, f"review round {round_no}: {after}", "review")
        return kept

    async def _operator_review(
        self, loop: "_Loop", round_no: int, findings: list[dict]
    ) -> tuple[str, list[dict]] | None:
        """Waits for the verdict of an operator who took this review over. Returns who, and the findings the
        fix run is asked for: the agent's that they kept, then their own. None when no one holds the review,
        or they released it: the round then decides on the agent's verdict, as it always did.

        The agent's review has finished and is posted by now, so the operator starts from its findings. The
        task waits, marked as waiting on the operator's review, for as long as they take: nothing releases a
        claim but the operator. Their verdict is posted on the pull request under their name. Behind a patch:
        a workflow from before it takes no claim.
        """
        if not self._task_id or not self._review_claim or not workflow.patched("operator-review"):
            return None
        who = self._review_claim
        self._operator_verdict = None
        count = f"{len(findings)} {'finding' if len(findings) == 1 else 'findings'}"
        await self._hold(
            True,
            f"review round {round_no}: {who} took the review over. The agent's review found {count}; "
            f"the round waits for {who}'s verdict",
            "operator_review",
        )
        await workflow.wait_condition(lambda: self._operator_verdict is not None or not self._review_claim)
        if self._operator_verdict is None:
            await self._hold(
                False,
                f"review round {round_no}: {who} gave the review back, so the agent's verdict decides",
                "operator_review",
            )
            return None
        verdict, self._operator_verdict, self._review_claim = json.loads(self._operator_verdict), None, ""
        keep = {int(n) for n in verdict.get("keep") or []}
        kept = [f for n, f in enumerate(findings, 1) if n in keep]
        own = [dict(f) for f in verdict.get("findings") or []]
        asked = kept + own
        lines = [f"**Review by {who}, round {round_no}**: {'changes needed' if asked else 'no change needed'}."]
        if findings:
            lines.append(f"Of the agent's {count}, {who} kept {len(kept)}.")
        for finding in own:
            where = finding.get("path") or "the change"
            line = f":{finding['line']}" if finding.get("line") else ""
            lines.append(f"- {where}{line}: {finding.get('body', '')}")
        await workflow.execute_activity(
            post_comment,
            CommentRequest(
                repo=loop.prepared.repo,
                pr_url=loop.published.pr_url,
                body="\n".join(lines),
                task_id=self._task_id,
            ),
            start_to_close_timeout=CODE_HOST_TIMEOUT,
            retry_policy=CODE_HOST_RETRY,
        )
        after = f"{len(asked)} findings go to the fix run" if asked else "no change is asked for, so the round is clean"
        await self._hold(False, f"review round {round_no}: {who} reviewed it; {after}", "operator_review")
        return who, asked

    async def _wait_for_base(self, loop: "_Loop", clean: str) -> str:
        """Waits until the base branch's newest commit's checks have passed, so nothing merges onto a broken
        base. Returns "" once they have, or why the merge is given up: they were still failed, or still
        running, after the task policy's merge_wait_minutes.

        A fix for a check that failed after the task's own merge does not wait: it is what repairs the base.
        Behind a patch: a workflow from before it merges without looking.
        """
        if loop.merge_wait_minutes <= 0 or loop.merge_fix or not workflow.patched("merge-base-health"):
            return ""
        base = loop.published.base
        waited, told = 0, False
        while True:
            health = await workflow.execute_activity(
                base_health,
                BaseHealthRequest(repo=loop.prepared.repo, base=base),
                start_to_close_timeout=CODE_HOST_TIMEOUT,
                retry_policy=CODE_HOST_RETRY,
            )
            if health.state == "healthy":
                return ""
            what = f"{base}'s checks at {health.sha[:12]} ({', '.join(health.checks)})"
            how = "have failed" if health.state == "failed" else "are still running"
            if waited >= loop.merge_wait_minutes:
                return (
                    f"{what} {how} after {loop.merge_wait_minutes} minutes, so it was not merged onto them: "
                    "merge it once they pass"
                )
            if not told and self._task_id:
                told = True
                await self._task_status("running", f"{clean}; waiting to merge: {what} {how}", loop.published.pr_url)
                workflow.logger.info("%s: waiting to merge: %s %s", workflow.info().workflow_id, what, how)
            await workflow.sleep(timedelta(minutes=BASE_HEALTH_POLL_MINUTES))
            waited += BASE_HEALTH_POLL_MINUTES

    def _still_clean(self, merged: MergeResult, clean: str, loop: "_Loop") -> str:
        """The clean verdict to merge on at the new head with no new review, or "" when it must be reviewed again.

        It stands when the head changed but the pull request's own diff did not: the base was brought in and
        touched none of its files. Behind a patch: workflows from before it review every new head."""
        if not merged.same_diff or not workflow.patched("same-diff-no-review"):
            return ""
        workflow.logger.info(
            "%s: the head moved to %s with the same diff, so the clean review stands",
            workflow.info().workflow_id,
            merged.head_sha[:12],
        )
        return f"{clean}; {loop.published.base} was brought in without changing the pull request's diff"

    async def _review(
        self,
        loop: "_Loop",
        reviewers: list[Candidate],
        round_no: int,
        previous: dict | None = None,
        passed_checks: list[str] | None = None,
    ) -> tuple[dict | None, str, str]:
        """One review round: each reviewer pair in order until one returns a verdict. previous is the round
        before's review, when it asked for changes: this round judges its findings first. passed_checks are
        the code host's checks that passed at this head, which the reviewer does not run again."""
        before = previous or {}
        prompt = await self._loop_prompt(
            loop.prompt_request(
                "review",
                round_no=round_no,
                previous_findings=list(before.get("findings") or []),
                previous_summary=str(before.get("summary") or ""),
                passed_checks=list(passed_checks or []),
            )
        )
        why = ""
        for reviewer in reviewers:
            if loop.left() <= 0 and not self._holds():
                return None, "", f"the task spent its {usd(loop.limit)} spend limit"
            harness, detail = await self._loop_run(loop, prompt, reviewer, "review", thinking=None)
            if harness is not None and harness.review:
                return harness.review, reviewer.label, ""
            why = f"{reviewer.label}: {detail}"
        return None, "", why

    async def _merge(self, loop: "_Loop", clean: str, asked_for: bool = False):
        """The merge after a clean review, when the goal allows it or an operator asked for it. Returns the
        merge's result, whose detail is the reason the task ends with."""
        auto = asked_for
        if loop.request.goal_id and not asked_for:
            auto = await workflow.execute_activity(
                goal_auto_merge,
                loop.request.goal_id,
                start_to_close_timeout=TASK_API_TIMEOUT,
                retry_policy=TASK_API_RETRY,
            )
        if not auto:
            why = "its goal has auto_merge off" if loop.request.goal_id else "the task has no goal to allow a merge"
            return MergeResult(state="ready", detail=f"{clean}; {why}, so it waits for an operator")
        if held := await self._wait_for_base(loop, clean):
            return MergeResult(state="blocked", detail=f"{clean}; {held}")
        merged = await workflow.execute_activity(
            merge_pr,
            MergeRequest(
                task_id=self._task_id,
                repo=loop.prepared.repo,
                pr_url=loop.published.pr_url,
                branch=loop.published.branch,
                base=loop.published.base,
                head_sha=loop.head,
                kind=version_kind(loop.request.task_type) if loop.merge_fix is False else "fix",
            ),
            start_to_close_timeout=CODE_HOST_TIMEOUT,
            retry_policy=CODE_HOST_RETRY,
        )
        workflow.logger.info("%s: merge at %s: %s", workflow.info().workflow_id, loop.head[:12], merged.state)
        if merged.state in ("blocked", "conflict"):
            workflow.logger.warning(
                "%s: the merge of %s at %s was %s: %s",
                self._where(),
                loop.published.pr_url,
                loop.head[:12],
                "refused" if merged.state == "blocked" else "a conflict",
                merged.detail,
            )
        if merged.state == "merged":
            merged.detail = f"{clean}; merged as {merged.merge_sha[:12]}"
        elif merged.state == "blocked":
            merged.detail = f"{clean}; GitHub would not merge it: {merged.detail}"
        return merged

    async def _fix(self, loop: "_Loop", fix: LoopPromptRequest, round_no: int) -> str:
        """One fix run on the pull request's branch, published to it. Returns why the loop must stop, or ""."""
        if loop.left() <= 0 and not self._holds():
            return f"the task spent its {usd(loop.limit)} spend limit before fix round {round_no}"
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
        branch: str | None = None,
    ) -> tuple[HarnessResult | None, str]:
        """A review or fix run on the pull request's branch, or on branch when given ("": the repo's default
        branch), recorded as the task's next attempt."""
        while True:
            harness, detail = await self._one_loop_run(loop, prompt, candidate, kind, thinking, merge_base, branch)
            # A run that stopped at its spend limit left its work unfinished: ask for more and run it again.
            if not (_out_of_budget(harness) and self._holds()):
                return harness, detail

    async def _one_loop_run(
        self,
        loop: "_Loop",
        prompt: str,
        candidate: Candidate,
        kind: str,
        thinking: str | None,
        merge_base: str,
        branch: str | None,
    ) -> tuple[HarnessResult | None, str]:
        """One review or fix run, once the task and its goal have the budget for it."""
        info = workflow.info()
        attempt = len(loop.attempts) + 1
        grant, loop.limit = await self._grant(
            loop.request.goal_id, loop.limit, loop.spent, _need(loop.attempts, kind), f"{kind} run {attempt}"
        )
        if grant is None:
            return None, f"the task spent its {usd(loop.limit)} spend limit"
        run_id = run_id_for(info.workflow_id, attempt)
        self._current_run_id = run_id
        harness, decision, detail, run_spent = await self._try(
            prompt,
            run_id,
            candidate,
            grant,
            loop.prepared.repo,
            thinking,
            branch=loop.published.branch if branch is None else branch,
            merge_base=merge_base,
            kind=kind,
            template=loop_template(kind),
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
        if harness is None or harness.status != "FINISHED":
            workflow.logger.warning(
                "%s: %s run %d on %s did not finish: %s", self._where(run_id), kind, attempt, candidate.label, detail
            )
        elif kind == "review" and not harness.review:
            workflow.logger.warning(
                "%s: review run %d on %s ended with no verdict: %s",
                self._where(run_id),
                attempt,
                candidate.label,
                detail,
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
        kind: str = "task",
        template: str = "",
    ) -> tuple[HarnessResult | None, str, str, Decimal]:
        """One run on one candidate. Returns the result, the decision, why, and what the run spent.

        A Task API task's run first records its prompt as run_prompt, so an operator sees what the run was given.
        """
        # Behind a patch: workflows started before it record no prompt, and replay as they ran.
        if self._task_id and workflow.patched("run-prompt"):
            await self._record(record_prompt, RunPromptRecord(self._task_id, run_id, kind, template, prompt))
        restarts = 0
        failed: ActivityError | None = None
        kind = ""
        while True:
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
                break
            except ActivityError as err:
                if isinstance(err.cause, CancelledError):
                    workflow.logger.info("%s: canceled, and run_harness has stopped pi", workflow.info().workflow_id)
                    raise
                kind = err.cause.type if isinstance(err.cause, ApplicationError) else type(err.cause).__name__
                # The worker stopped under the run: a deploy restarts it on every merge. That is not the
                # run's failure. The run goes on in its sandbox, so the same run is taken up again once the
                # worker is back: the activity re-attaches to it by its run id. Behind a patch, which is
                # asked for only here, so a workflow that never meets a restart replays as it ran.
                if kind == WORKER_SHUTDOWN and restarts < WORKER_RESTARTS and workflow.patched("worker-restart"):
                    restarts += 1
                    workflow.logger.warning(
                        "%s: the worker restarted under run %s; taking the run up again (%d of %d)",
                        self._where(),
                        run_id,
                        restarts,
                        WORKER_RESTARTS,
                    )
                    await workflow.sleep(WORKER_RESTART_WAIT)
                    continue
                failed = err
                break
        if failed is not None:
            err = failed
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
        # The merge commit, once the review loop merges; the task policy's fix rounds, once the loop reads them.
        self.merge_sha = ""
        self.max_rounds = 0
        # Whether the pull request in the loop is a fix for a check that failed after the task's own merge.
        self.merge_fix = False
        # How long a merge waits for the base's own checks, from the task policy; 0: it does not wait.
        self.merge_wait_minutes = 0

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


def version_kind(task_type: str) -> str:
    """What kind of change a task's merge is, for a version worked out from history: a feature task is a
    feature, a docs or test task ships nothing, and any other task of the Task API is a fix. A task with no
    type (one started by hand, outside the Task API) says nothing, and the commit's subject decides."""
    if not task_type:
        return ""
    if task_type == "feature":
        return "feature"
    return "none" if task_type in ("docs", "test") else "fix"


def _need(attempts: list[Attempt], kind: str) -> Decimal:
    """What a run of this kind (task, review or fix) is likely to need: what the task's last run of that kind
    spent, or nothing when it has made none."""
    same = [a for a in attempts if (a.tier if a.tier in ("review", "fix") else "task") == kind]
    return Decimal(same[-1].spent_usd) if same else Decimal(0)


def _out_of_budget(harness: HarnessResult | None) -> bool:
    """Whether the run stopped because its spend limit was reached."""
    return harness is not None and harness.status != "FINISHED" and harness.end_reason == "spend_limit"


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
