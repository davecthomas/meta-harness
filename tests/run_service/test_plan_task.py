"""A goal's planning task: its run proposes issues and changes no file, so it ends with no publish and no review."""

from temporalio import activity
from temporalio.activity import _Definition
from temporalio.testing import ActivityEnvironment
from temporalio.worker import Worker

from meta_harness.run_service import activities, code_host
from meta_harness.run_service.shared import RunTaskInput, TaskStatusRequest
from meta_harness.run_service.workflows import RunTask

from .test_replay import _task_activities
from .test_review_loop import QUEUE, Script, env  # noqa: F401 - env is a fixture


async def test_a_planning_task_closes_after_its_run_with_no_publish_and_no_review(env):  # noqa: F811
    script = Script()
    statuses: list[TaskStatusRequest] = []

    @activity.defn(name="set_task_status")
    async def set_task_status(request: TaskStatusRequest) -> str:
        statuses.append(request)
        return request.status

    base = [*script.activities(), *_task_activities(True, "g-1", "plan")]
    kept = [a for a in base if _Definition.from_callable(a).name != "set_task_status"]
    async with Worker(env.client, task_queue=QUEUE, workflows=[RunTask], activities=[*kept, set_task_status]):
        result = await env.client.execute_workflow(
            RunTask.run, RunTaskInput(task="", repo="", task_id="t-plan"), id="run-task-t-plan", task_queue=QUEUE
        )

    assert result.outcome == "planned" and result.published is None
    assert len(script.runs) == 1 and script.publishes == 0 and not script.posted
    assert statuses[-1].status == "closed" and "the planner reads its drafts" in statuses[-1].reason


async def test_a_planning_tasks_prompt_wraps_the_planners_request_and_names_no_checks(monkeypatch):
    monkeypatch.setattr(code_host, "check_repo", lambda repo: None)
    request = RunTaskInput(
        task="Plan this goal as issues.\n\n--- goal ---\nAdd exports\n--- end of goal ---",
        title="Plan: Add exports",
        repo="owner/repo",
        task_id="t-1",
        task_type="plan",
    )

    prepared = await ActivityEnvironment().run(activities.prepare, request)

    assert prepared.template == "plan-v1" and prepared.repo == "owner/repo"
    assert "You are planning work in the repository owner/repo" in prepared.prompt
    assert "Plan: Add exports" in prepared.prompt and "Add exports\n--- end of goal ---" in prepared.prompt
    assert "Do not change, create or delete any file" in prepared.prompt
    assert "$" not in prepared.prompt and "Acceptance checks:" not in prepared.prompt
