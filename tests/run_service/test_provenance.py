"""Each run of a task carries its provenance: built by the workflow, recorded before the run, and published with it."""

import jsonschema
import pytest
from temporalio import activity
from temporalio.activity import _Definition
from temporalio.testing import ActivityEnvironment
from temporalio.worker import Worker

from meta_harness.events import types
from meta_harness.run_service import activities, prompt_activities, provenance
from meta_harness.run_service.prompt_activities import RunPromptRecord
from meta_harness.run_service.shared import PublishRequest, RunTaskInput, TaskSpec
from meta_harness.run_service.workflows import RunTask

from .test_replay import REPO, _task_activities
from .test_review_loop import CHANGES, CLEAN, QUEUE, Script, env  # noqa: F401 - env is a fixture

ORIGIN = provenance.TaskOrigin(
    task_id="t-1", goal_id="g-1", issue="github:owner/repo#7", task_type="fix", repo="owner/repo", retries=1
)


def _schema():
    return types.by_key()[(provenance.EVENT_TYPE, 1)].json_schema


def test_a_record_names_the_task_the_run_and_the_run_it_follows():
    made = provenance.build(ORIGIN, "t-1:run-3", "fix", "harness:a/b:c", "fix-v1", tier="complex", follows="t-1:run-2")

    assert made == {
        "task_id": "t-1",
        "run_id": "t-1:run-3",
        "attempt": 3,
        "kind": "fix",
        "candidate": "harness:a/b:c",
        "template": "fix-v1",
        # A review or fix run has no route tier of its own.
        "tier": None,
        "follows": "t-1:run-2",
        "workflow": 2,
        "goal_id": "g-1",
        "issue": "github:owner/repo#7",
        "parent_task_id": None,
        "task_type": "fix",
        "repo": "owner/repo",
    }
    provenance.check(made, "t-1")
    jsonschema.validate(made, _schema())


def test_a_first_run_follows_nothing_and_keeps_its_tier():
    made = provenance.build(
        provenance.TaskOrigin(task_id="t-1"), "t-1:run-1", "task", "harness:a/b:c", "repo-task-v1", "standard"
    )

    assert (made["follows"], made["tier"], made["workflow"], made["goal_id"]) == (None, "standard", 1, None)
    provenance.check(made, "t-1")
    jsonschema.validate(made, _schema())


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"task_id": "t-2"}, "not 't-1'"),
        ({"run_id": "t-2:run-1", "attempt": 1}, "not a run of task t-1"),
        ({"attempt": 4}, "is run 3, not run 4"),
        ({"kind": "plan"}, "kind is one of"),
        ({"candidate": " "}, "names no candidate"),
        ({"workflow": 0}, "numbered from 1"),
        ({"follows": "t-2:run-2"}, "not a run of the same task"),
        ({"follows": "t-1:run-3"}, "not an earlier run"),
        ({"follows": "t-1:run-5"}, "not an earlier run"),
    ],
)
def test_a_record_that_does_not_hold_together_is_refused(change, why):
    made = provenance.build(ORIGIN, "t-1:run-3", "fix", "harness:a/b:c", "fix-v1", follows="t-1:run-2")
    with pytest.raises(provenance.ProvenanceError, match=why):
        provenance.check({**made, **change}, "t-1")


def test_a_hand_started_workflows_runs_are_its_tasks():
    made = provenance.build(provenance.TaskOrigin(task_id="t-1"), "run-task-t-1:run-2", "review", "x", "review-v4")
    provenance.check({**made, "follows": "run-task-t-1:run-1"}, "t-1")


def test_the_pull_request_and_its_commit_carry_the_provenance():
    made = provenance.build(ORIGIN, "t-1:run-1", "task", "harness:a/b:c", "repo-task-v1", "standard")
    request = PublishRequest(
        run_id="t-1:run-1",
        repo="owner/repo",
        ticket="owner/repo#7",
        title="Fix the bug",
        candidate="harness:a/b:c",
        summary="Fixed it.",
        spent_usd="1.00",
        task_id="t-1",
        provenance=made,
    )

    body = activities._pr_body(request)
    assert "- Task: `t-1`" in body
    assert "- Goal: `g-1`" in body
    assert "- Issue: `github:owner/repo#7`" in body
    assert "- Workflow: 2 (the task was sent back once)" in body
    assert "Follows run" not in body

    message = activities._commit_message(request)
    # Trailers are the message's last paragraph, after the issue it closes.
    assert message.endswith(
        "Closes owner/repo#7\n\n"
        "Meta-Harness-Task: t-1\n"
        "Meta-Harness-Run: t-1:run-1\n"
        "Meta-Harness-Goal: g-1\n"
        "Meta-Harness-Issue: github:owner/repo#7"
    )


def test_a_run_outside_the_task_api_publishes_as_before():
    request = PublishRequest(
        run_id="r:run-1", repo="owner/repo", ticket="", title="T", candidate="c", summary=None, spent_usd="0"
    )
    assert activities._commit_message(request) == "T\n\nRun r:run-1 on c."
    assert "- Task:" not in activities._pr_body(request)


def _client(sent: list[dict]):
    class Client:
        source = "run"

        def append(self, batch):
            sent.extend(batch)
            return [len(sent)]

    return Client()


async def test_record_prompt_appends_the_provenance_before_the_prompt(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(prompt_activities.events, "client", lambda: _client(sent))
    made = provenance.build(ORIGIN, "t-1:run-2", "review", "harness:a/b:c", "review-v4", follows="t-1:run-1")
    record = RunPromptRecord("t-1", "t-1:run-2", "review", "review-v4", "Review it.", provenance=made)

    await ActivityEnvironment().run(prompt_activities.record_prompt, record)
    await ActivityEnvironment().run(prompt_activities.record_prompt, record)

    assert [e["event_type"] for e in sent] == ["run_provenance", "run_prompt"] * 2
    event = sent[0]
    assert event["payload"] == made
    assert (event["task_id"], event["run_id"]) == ("t-1", "t-1:run-2")
    # A retry of the activity appends the same event id, which the event service ignores.
    assert sent[2]["event_id"] == event["event_id"]
    jsonschema.validate(event["payload"], _schema())
    # The same run number in the task's next workflow is another run, so another event.
    again = provenance.build(
        provenance.TaskOrigin(**{**vars(ORIGIN), "retries": 2}), "t-1:run-2", "review", "x", "review-v4"
    )
    await ActivityEnvironment().run(
        prompt_activities.record_prompt, RunPromptRecord("t-1", "t-1:run-2", "review", "review-v4", "R", again)
    )
    assert sent[4]["event_id"] != event["event_id"]


async def test_a_record_that_does_not_hold_together_is_not_appended_and_the_run_goes_on(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(prompt_activities.events, "client", lambda: _client(sent))
    wrong = provenance.build(ORIGIN, "t-1:run-2", "task", "c", "repo-task-v1", follows="t-1:run-2")
    other = provenance.build(ORIGIN, "t-1:run-1", "task", "c", "repo-task-v1")

    for made in (wrong, other):
        await ActivityEnvironment().run(
            prompt_activities.record_prompt, RunPromptRecord("t-1", "t-1:run-2", "task", "repo-task-v1", "P", made)
        )

    assert [e["event_type"] for e in sent] == ["run_prompt", "run_prompt"]


async def test_every_run_of_a_task_carries_its_tasks_origin_and_the_run_before_it(env):  # noqa: F811 - the fixture
    script = Script(verdicts=[CHANGES, CLEAN], fix_commits=["c1"])
    records: list[RunPromptRecord] = []
    tasks = _task_activities(True, "g-1")

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
            goal_id="g-1",
            retries=1,
            task_type="fix",
            external_ref="github:owner/repo#1",
            parent_task_id="t-parent",
        )

    @activity.defn(name="record_prompt")
    async def record_prompt(record: RunPromptRecord) -> None:
        records.append(record)

    replaced = ("load_task", "record_prompt")
    kept = [a for a in [*script.activities(), *tasks] if _Definition.from_callable(a).name not in replaced]
    async with Worker(env.client, task_queue=QUEUE, workflows=[RunTask], activities=[*kept, load_task, record_prompt]):
        result = await env.client.execute_workflow(
            RunTask.run, RunTaskInput(task_id="t-prov"), id="run-task-t-prov", task_queue=QUEUE
        )

    made = [r.provenance for r in records]
    run_ids = [a.run_id for a in result.attempts]
    assert [m["run_id"] for m in made] == run_ids
    assert [m["kind"] for m in made] == ["task", "review", "fix", "review"]
    assert [m["follows"] for m in made] == [None, *run_ids[:-1]]
    for m in made:
        provenance.check(m, "t-prov")
        assert (m["goal_id"], m["issue"], m["parent_task_id"], m["workflow"]) == (
            "g-1",
            "github:owner/repo#1",
            "t-parent",
            2,
        )
    # The task's own run and its fix run publish, each with its own provenance.
    published = [(p.run_id, p.provenance["run_id"], p.provenance["kind"]) for p in script.publish_requests]
    assert published == [(run_ids[0], run_ids[0], "task"), (run_ids[2], run_ids[2], "fix")]
