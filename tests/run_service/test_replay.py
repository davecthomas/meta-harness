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

which writes the scenarios below into histories/ beside the ones there. Keep
the old files: they are the workflows a deploy would find running.
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
