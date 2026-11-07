"""The RunTask workflow's run loop, against stand-in activities on Temporal's time-skipping test server.

Each test scripts how each candidate's run ends and checks the Decide step:
a finished run ends the loop, an infrastructure failure moves to the next
candidate, a limit ends the task, and a used-up tier escalates once.
"""

import uuid
from dataclasses import dataclass, field

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from meta_harness.run_service.shared import (
    Candidate,
    CleanupResult,
    HarnessRequest,
    HarnessResult,
    PreparedRun,
    RouteRequest,
    RouteResult,
    RunTaskInput,
)
from meta_harness.run_service.workflows import HARNESS_GRACE, RunTask

QUEUE = "test-run-task"


def _candidate(tier: str, index: int, minutes: int) -> Candidate:
    return Candidate(
        label=f"harness:pi-{tier}/claude:m{index}",
        profile_name=f"pi-{tier}",
        engine="claude",
        model=f"m{index}",
        profile={"name": f"pi-{tier}", "tools": ["read"], "max_turns": 10, "max_minutes": minutes, "image": "x"},
    )


TIERS = {
    "standard": [_candidate("standard", 0, 45), _candidate("standard", 1, 45)],
    "complex": [_candidate("complex", 0, 120)],
}
THINKING = {"standard": "medium", "complex": "high"}


@dataclass
class Script:
    """How each run ends, by candidate label, and what the stand-in activities saw."""

    ends: dict[str, tuple[str, str]]
    routes: list[RouteRequest] = field(default_factory=list)
    runs: list[tuple[HarnessRequest, float]] = field(default_factory=list)

    def activities(self):
        @activity.defn(name="prepare")
        async def prepare(request: RunTaskInput) -> PreparedRun:
            return PreparedRun(template="inline", prompt=request.task)

        @activity.defn(name="route")
        async def route(request: RouteRequest) -> RouteResult:
            self.routes.append(request)
            tier = request.floor or "standard"
            return RouteResult(
                tier=tier,
                method="floor" if request.floor else "classifier",
                confidence=0.9,
                candidates=TIERS[tier],
                tiers=["standard", "complex"],
                on_exhausted="escalate",
                classifier_usd="0",
                config_id="c",
                thinking=THINKING[tier],
            )

        @activity.defn(name="run_harness")
        async def run_harness(request: HarnessRequest) -> HarnessResult:
            self.runs.append((request, activity.info().start_to_close_timeout.total_seconds()))
            status, end_reason = self.ends[request.candidate.label]
            return HarnessResult(
                attempt=1,
                run_id=request.run_id,
                status=status,
                end_reason=end_reason,
                reattached=False,
                turns=3,
                failed_calls=0,
                tokens_in=10,
                tokens_out=5,
                usd=1.0,
                files_changed=[],
                answer="done",
                spend_limit_usd=request.spend_limit_usd,
                grant_spent_usd="1.00",
            )

        @activity.defn(name="cleanup_run")
        async def cleanup_run(run_id: str) -> CleanupResult:
            return CleanupResult(grant_spent_usd="0")

        return [prepare, route, run_harness, cleanup_run]


@pytest.fixture
async def env():
    try:
        environment = await WorkflowEnvironment.start_time_skipping()
    except Exception as err:  # noqa: BLE001 - the test server is a download; without it these tests cannot run
        pytest.skip(f"Temporal's time-skipping test server is not available: {err}")
    async with environment:
        yield environment


async def _run(env: WorkflowEnvironment, script: Script, spend: str = "10.00"):
    async with Worker(env.client, task_queue=QUEUE, workflows=[RunTask], activities=script.activities()):
        return await env.client.execute_workflow(
            RunTask.run,
            RunTaskInput(task="Fix the bug.", spend_limit_usd=spend),
            id=f"run-task-test-{uuid.uuid4().hex[:8]}",
            task_queue=QUEUE,
        )


async def test_a_finished_run_ends_the_loop_with_the_tier_thinking_and_the_profile_timeout(env):
    script = Script(ends={TIERS["standard"][0].label: ("FINISHED", "completed")})

    result = await _run(env, script)

    assert [a.decision for a in result.attempts] == ["finished"]
    request, timeout_seconds = script.runs[0]
    assert request.thinking == "medium"
    assert timeout_seconds == 45 * 60 + HARNESS_GRACE.total_seconds()


async def test_an_infrastructure_failure_moves_to_the_next_candidate_with_what_is_left(env):
    script = Script(
        ends={
            TIERS["standard"][0].label: ("ERROR", "model_error"),
            TIERS["standard"][1].label: ("FINISHED", "completed"),
        }
    )

    result = await _run(env, script, spend="5.00")

    assert [a.decision for a in result.attempts] == ["next_candidate", "finished"]
    assert [r.spend_limit_usd for r, _ in script.runs] == ["5.00", "4.00"]
    assert result.spent_usd == "2.00"


async def test_a_run_that_ends_on_a_limit_ends_the_task(env):
    script = Script(ends={TIERS["standard"][0].label: ("ERROR", "max_turns")})

    with pytest.raises(WorkflowFailureError) as failure:
        await _run(env, script)

    assert "ended for good" in str(failure.value.cause)
    assert len(script.runs) == 1


async def test_a_used_up_tier_escalates_once_with_the_higher_tier_thinking_and_timeout(env):
    script = Script(
        ends={
            TIERS["standard"][0].label: ("ERROR", "model_error"),
            TIERS["standard"][1].label: ("ERROR", "harness_error"),
            TIERS["complex"][0].label: ("FINISHED", "completed"),
        }
    )

    result = await _run(env, script)

    assert [r.floor for r in script.routes] == ["", "complex"]
    assert [a.tier for a in result.attempts] == ["standard", "standard", "complex"]
    request, timeout_seconds = script.runs[-1]
    assert request.thinking == "high"
    assert timeout_seconds == 120 * 60 + HARNESS_GRACE.total_seconds()


async def test_the_top_tier_used_up_fails_the_task(env):
    script = Script(
        ends={
            TIERS["standard"][0].label: ("ERROR", "model_error"),
            TIERS["standard"][1].label: ("ERROR", "model_error"),
            TIERS["complex"][0].label: ("ERROR", "server_shutdown"),
        }
    )

    with pytest.raises(WorkflowFailureError) as failure:
        await _run(env, script)

    assert "every candidate failed (3 runs)" in str(failure.value.cause)
