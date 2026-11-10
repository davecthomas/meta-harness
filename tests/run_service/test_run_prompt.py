"""Each run's prompt is recorded as run_prompt before the run starts: the task run's, and each review and fix run's."""

from types import SimpleNamespace

import httpx
import jsonschema
import pytest
from temporalio import activity
from temporalio.activity import _Definition
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from temporalio.worker import Worker

from meta_harness.events import types
from meta_harness.run_service import prompt_activities
from meta_harness.run_service.prompt_activities import RunPromptRecord, loop_template
from meta_harness.run_service.shared import HarnessRequest, RunTaskInput
from meta_harness.run_service.workflows import RunTask

from .test_replay import _task_activities
from .test_review_loop import CHANGES, CLEAN, QUEUE, Script, env  # noqa: F401 - env is a fixture


async def test_every_run_of_a_task_records_its_prompt_before_it_starts(env):  # noqa: F811 - the fixture
    script = Script(verdicts=[CHANGES, CLEAN], fix_commits=["c1"])
    # What happened, in order: each prompt recorded and each run started.
    order: list[tuple[str, str]] = []
    records: list[RunPromptRecord] = []
    activities = [*script.activities(), *_task_activities(True, "")]
    by_name = {_Definition.from_callable(a).name: a for a in activities}

    @activity.defn(name="record_prompt")
    async def record_prompt(record: RunPromptRecord) -> None:
        records.append(record)
        order.append(("prompt", record.run_id))

    @activity.defn(name="run_harness")
    async def run_harness(request: HarnessRequest):
        order.append(("run", request.run_id))
        return await by_name["run_harness"](request)

    workers = [a for a in activities if _Definition.from_callable(a).name not in ("record_prompt", "run_harness")]
    async with Worker(
        env.client, task_queue=QUEUE, workflows=[RunTask], activities=[*workers, record_prompt, run_harness]
    ):
        result = await env.client.execute_workflow(
            RunTask.run, RunTaskInput(task="", repo="", task_id="t-prompt"), id="run-task-t-prompt", task_queue=QUEUE
        )

    assert result.outcome == "clean"
    assert [(r.kind, r.template, r.prompt) for r in records] == [
        ("task", "repo-task-v1", "task"),
        ("review", "review-v2", "review"),
        ("fix", "fix-v1", "fix"),
        ("review", "review-v2", "review"),
    ]
    assert all(r.task_id == "t-prompt" for r in records)
    assert [r.run_id for r in records] == [a.run_id for a in result.attempts]
    # Each run's prompt is recorded just before that run starts.
    assert order == [step for r in records for step in (("prompt", r.run_id), ("run", r.run_id))]


async def test_record_prompt_appends_run_prompt_on_the_task_with_the_run_id(monkeypatch):
    sent: list[list[dict]] = []

    class Client:
        source = "run"

        def append(self, batch):
            sent.append(batch)
            return [len(sent)]

        def emit(self, *args, **kwargs):
            raise AssertionError("run_prompt must be appended at once, not queued in the outbox")

    monkeypatch.setattr(prompt_activities.events, "client", lambda: Client())
    record = RunPromptRecord("t-1", "run-task-t-1:run-2", "review", "review-v1", "Review the pull request.")

    await ActivityEnvironment().run(prompt_activities.record_prompt, record)
    await ActivityEnvironment().run(prompt_activities.record_prompt, record)

    [event] = sent[0]
    assert (event["event_type"], event["source"]) == ("run_prompt", "run")
    payload = event["payload"]
    assert payload == {
        "run_id": "run-task-t-1:run-2",
        "kind": "review",
        "template": "review-v1",
        "prompt": "Review the pull request.",
    }
    assert (event["task_id"], event["run_id"]) == ("t-1", "run-task-t-1:run-2")
    # A Temporal retry appends the same event id, which the event service ignores.
    assert sent[1][0]["event_id"] == event["event_id"]
    jsonschema.validate(payload, types.by_key()[("run_prompt", 1)].json_schema)


async def test_record_prompt_fails_when_the_event_service_does_not_store_it(monkeypatch):
    def unreachable(batch):
        raise httpx.ConnectError("refused")

    def rejected(batch):
        request = httpx.Request("POST", "http://events/v1/events")
        raise httpx.HTTPStatusError("422", request=request, response=httpx.Response(422, request=request))

    record = RunPromptRecord("t-1", "run-task-t-1:run-1", "task", "repo-task-v1", "Do the task.")
    # Unreachable: the activity raises, so Temporal retries it before the run starts.
    monkeypatch.setattr(prompt_activities.events, "client", lambda: SimpleNamespace(source="run", append=unreachable))
    with pytest.raises(httpx.ConnectError):
        await ActivityEnvironment().run(prompt_activities.record_prompt, record)
    # Rejected: a retry cannot fix it.
    monkeypatch.setattr(prompt_activities.events, "client", lambda: SimpleNamespace(source="run", append=rejected))
    with pytest.raises(ApplicationError) as err:
        await ActivityEnvironment().run(prompt_activities.record_prompt, record)
    assert err.value.non_retryable


def test_a_loop_runs_template_is_named_by_its_kind():
    assert (loop_template("review"), loop_template("fix")) == ("review-v2", "fix-v1")
