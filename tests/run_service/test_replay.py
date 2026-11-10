"""Running workflows survive a deploy: recorded RunTask histories replay against the current workflow code.

A deploy restarts the worker, and Temporal replays every running workflow's
history against the new code. A change to which activities, timers or
signals RunTask runs, or their order, no longer matches a history recorded
before it, and Temporal retries that workflow task without end. So a change
like that goes behind `workflow.patched` (AGENTS.md), and this test replays
the histories under tests/run_service/histories against the code as it is.

The histories are recorded from the review loop's scripted runs, on the
time-skipping test server. To add one after a workflow change that is meant
to be recorded (behind its patch), run:

    MH_RECORD_HISTORIES=1 uv run pytest -q tests/run_service/test_replay.py

which records each scenario below that has no file yet. Recorded files are
never rewritten: they are the workflows a deploy would find running.
"""

import json
import os
from pathlib import Path

import pytest
from temporalio import activity
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer, Worker

from meta_harness.run_service.shared import (
    AttemptRecord,
    PublishedRecord,
    RouteRecord,
    RunTaskInput,
    TaskSpec,
    TaskStatusRequest,
)
from meta_harness.run_service.workflows import RunTask

from .test_review_loop import CHANGES, CLEAN, QUEUE, REPO, Script, env  # noqa: F401 - env is a fixture

HISTORIES = Path(__file__).with_name("histories")

# What each recorded history ran: the review loop's verdicts and fix commits, and whether its goal merges.
# Every one is a Task API task, as every deployed RunTask is: it loads the task, moves its status, and records.
SCENARIOS = {
    "clean-review-merged": {"verdicts": [CLEAN], "goal_id": "g-1", "auto_merge": True},
    "changes-fix-clean-waiting": {"verdicts": [CHANGES, CLEAN], "fix_commits": ["c1"], "goal_id": "g-1"},
    "no-verdict-unresolved": {"verdicts": []},
    # Recorded after the operator-messages patch, so the patched path replays too.
    "patched-operator-messages-merged": {"verdicts": [CLEAN], "goal_id": "g-1", "auto_merge": True},
    # Recorded after the merge-checks patch: a failed check on the merge commit, fixed by a new pull request.
    "patched-merge-checks-fixed": {
        "verdicts": [CLEAN, CLEAN],
        "goal_id": "g-1",
        "auto_merge": True,
        "merge_checks": ["failed", "passed"],
    },
}


def _task_activities(review: bool, goal_id: str) -> list:
    """Stand-ins for the task service and the record activities, which a Task API task runs."""

    @activity.defn(name="load_task")
    async def load_task(task_id: str) -> TaskSpec:
        return TaskSpec(
            task_id=task_id,
            status="queued",
            repo=REPO,
            title="Fix the bug",
            description="The bug is in app.py.",
            checks=["pytest -q"],
            pin="",
            floor="",
            ceiling="",
            spend_limit_usd="10.00",
            closes="owner/repo#1",
            goal_id=goal_id,
        )

    @activity.defn(name="set_task_status")
    async def set_task_status(request: TaskStatusRequest) -> str:
        return request.status

    @activity.defn(name="record_route")
    async def record_route(record: RouteRecord) -> None:
        return None

    @activity.defn(name="record_attempt")
    async def record_attempt(record: AttemptRecord) -> None:
        return None

    @activity.defn(name="record_published")
    async def record_published(record: PublishedRecord) -> None:
        return None

    return [load_task, set_task_status, record_route, record_attempt, record_published]


def _histories() -> list[Path]:
    return sorted(HISTORIES.glob("*.json"))


@pytest.mark.skipif(not os.environ.get("MH_RECORD_HISTORIES"), reason="records histories only when asked")
@pytest.mark.parametrize("name", sorted(SCENARIOS))
async def test_record_a_history(env, name):  # noqa: F811 - env is the fixture imported above
    if (HISTORIES / f"{name}.json").exists():
        pytest.skip("recorded already: an old history is what a deploy finds running, so it stays as it is")
    scenario = dict(SCENARIOS[name])
    review = scenario.pop("review", True)
    goal_id = scenario.pop("goal_id", "")
    auto_merge = scenario.pop("auto_merge", False)
    script = Script(**scenario)
    script.auto_merge = auto_merge
    workflow_id = f"run-task-{name}"
    activities = [*script.activities(), *_task_activities(review, goal_id)]
    async with Worker(env.client, task_queue=QUEUE, workflows=[RunTask], activities=activities):
        handle = await env.client.start_workflow(
            RunTask.run,
            RunTaskInput(task="", repo="", task_id=f"t-{name}"),
            id=workflow_id,
            task_queue=QUEUE,
        )
        try:
            await handle.result()
        except Exception:  # noqa: BLE001 - a run that ends in failure is a history all the same
            pass
        history = await handle.fetch_history()
    HISTORIES.mkdir(exist_ok=True)
    (HISTORIES / f"{name}.json").write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n")


@pytest.mark.parametrize("path", _histories(), ids=[p.stem for p in _histories()])
async def test_a_recorded_history_replays_against_the_current_workflow(path):
    history = WorkflowHistory.from_json(f"run-task-{path.stem}", path.read_text())
    await Replayer(workflows=[RunTask]).replay_workflow(history)


def test_there_are_histories_to_replay():
    assert _histories(), f"no recorded histories in {HISTORIES}: record them as the module docstring says"


async def test_an_operator_message_reaches_the_run_in_progress(env):  # noqa: F811 - env is the fixture imported above
    import asyncio

    from temporalio.activity import _Definition

    from meta_harness.run_service.shared import DeliverRequest, HarnessRequest, OperatorMessage

    script = Script(verdicts=[CLEAN])
    delivered: list[DeliverRequest] = []
    reached = asyncio.Event()
    activities = [*script.activities(), *_task_activities(True, "")]
    original = next(a for a in activities if _Definition.from_callable(a).name == "run_harness")

    @activity.defn(name="run_harness")
    async def run_harness(request: HarnessRequest):
        # The first run waits for the operator's message, so the message arrives while it is in progress.
        if not delivered:
            await asyncio.wait_for(reached.wait(), timeout=30)
        return await original(request)

    @activity.defn(name="deliver_message")
    async def deliver_message(request: DeliverRequest) -> str:
        delivered.append(request)
        reached.set()
        return "delivered"

    activities = [a for a in activities if _Definition.from_callable(a).name != "run_harness"]
    async with Worker(
        env.client, task_queue=QUEUE, workflows=[RunTask], activities=[*activities, run_harness, deliver_message]
    ):
        handle = await env.client.start_workflow(
            RunTask.run, RunTaskInput(task="", repo="", task_id="t-msg"), id="run-task-t-msg", task_queue=QUEUE
        )
        for _ in range(200):
            if await handle.query(RunTask.current_run_id):
                break
            await asyncio.sleep(0.05)
        await handle.signal(
            RunTask.operator_message, OperatorMessage(message_id=7, text="Use the existing helper.", when="next_turn")
        )
        await handle.result()

    assert [(d.message.message_id, d.message.text) for d in delivered] == [(7, "Use the existing helper.")]
    assert delivered[0].run_id.endswith(":run-1")


async def test_parallel_attempts_wait_for_the_operators_pick_and_publish_it(env):  # noqa: F811 - the fixture
    from temporalio.activity import _Definition

    from meta_harness.run_service.shared import OperatorMessage, PublishRequest, RouteRequest, RouteResult

    from .test_review_loop import AUTHOR, REVIEWER_B

    script = Script(verdicts=[CLEAN])
    published: list[PublishRequest] = []
    statuses: list[str] = []
    activities = [*script.activities(), *_task_activities(True, "")]
    by_name = {_Definition.from_callable(a).name: a for a in activities}

    @activity.defn(name="load_task")
    async def load_task(task_id: str) -> TaskSpec:
        spec = await by_name["load_task"](task_id)
        spec.attempts = 2
        return spec

    @activity.defn(name="route")
    async def route(request: RouteRequest) -> RouteResult:
        result = await by_name["route"](request)
        result.candidates = [AUTHOR, REVIEWER_B]
        return result

    @activity.defn(name="publish")
    async def publish(request: PublishRequest):
        published.append(request)
        return await by_name["publish"](request)

    @activity.defn(name="set_task_status")
    async def set_task_status(request: TaskStatusRequest) -> str:
        statuses.append(request.reason)
        return request.status

    replaced = {"load_task", "route", "publish", "set_task_status"}
    workers = [a for a in activities if _Definition.from_callable(a).name not in replaced]
    workers += [load_task, route, publish, set_task_status]
    async with Worker(env.client, task_queue=QUEUE, workflows=[RunTask], activities=workers):
        handle = await env.client.start_workflow(
            RunTask.run, RunTaskInput(task="", repo="", task_id="t-par"), id="run-task-t-par", task_queue=QUEUE
        )
        await handle.signal(RunTask.operator_message, OperatorMessage(message_id=9, text="2", when="choose"))
        result = await handle.result()
        history = await handle.fetch_history()

    assert [a.candidate for a in result.attempts[:2]] == [AUTHOR.label, REVIEWER_B.label]
    assert published[0].run_id.endswith(":run-2") and published[0].candidate == REVIEWER_B.label
    assert any("waiting for an operator to compare them" in s for s in statuses)
    path = HISTORIES / "patched-parallel-attempts.json"
    if os.environ.get("MH_RECORD_HISTORIES") and not path.exists():
        path.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n")
