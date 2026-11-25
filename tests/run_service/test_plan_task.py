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


async def test_a_plans_review_is_told_to_review_and_not_to_draft_issues(monkeypatch):
    """A plan's adversarial review is a planning task pinned to a reviewer. Wrapped in the planning prompt it
    was told to propose issues with a tool it does not have, and to ignore the instructions in its text."""
    from meta_harness.run_service import routing
    from meta_harness.run_service.shared import Candidate

    monkeypatch.setattr(code_host, "check_repo", lambda repo: None)
    reviewer = Candidate(
        "harness:pi-review/openai-responses:m",
        "pi-review",
        "openai-responses",
        "m",
        {"tools": ["read", "submit_review"]},
    )
    monkeypatch.setattr(routing, "pinned", lambda pin: reviewer)
    request = RunTaskInput(
        task="Your job is to find what will make this plan fail.\n\n--- goal ---\nAdd exports\n--- end of goal ---",
        title="Review the plan: Add exports",
        repo="owner/repo",
        task_id="t-2",
        task_type="plan",
        pin=reviewer.label,
    )

    prepared = await ActivityEnvironment().run(activities.prepare, request)

    assert prepared.template == "plan-review-v1"
    assert "You are reviewing a plan for work in the repository owner/repo" in prepared.prompt
    assert "create_issue" not in prepared.prompt and "End by calling submit_review once" in prepared.prompt
    assert 'Its first part, up to the line "--- goal ---", is your instructions' in prepared.prompt
    assert "Your job is to find what will make this plan fail." in prepared.prompt and "$" not in prepared.prompt
    # A planning task pinned to the planning profile is still told to plan.
    planner = Candidate("harness:pi-plan/claude:m", "pi-plan", "claude", "m", {"tools": ["read", "create_issue"]})
    monkeypatch.setattr(routing, "pinned", lambda pin: planner)
    assert (await ActivityEnvironment().run(activities.prepare, request)).template == "plan-v1"


async def test_a_deploys_smoke_task_closes_after_its_run_with_no_publish_no_review_and_no_hold(env):  # noqa: F811
    script = Script()
    statuses: list[TaskStatusRequest] = []

    @activity.defn(name="set_task_status")
    async def set_task_status(request: TaskStatusRequest) -> str:
        statuses.append(request)
        return request.status

    base = [*script.activities(), *_task_activities(True, "", "smoke")]
    kept = [a for a in base if _Definition.from_callable(a).name != "set_task_status"]
    async with Worker(env.client, task_queue=QUEUE, workflows=[RunTask], activities=[*kept, set_task_status]):
        result = await env.client.execute_workflow(
            RunTask.run, RunTaskInput(task="", repo="", task_id="t-smoke"), id="run-task-t-smoke", task_queue=QUEUE
        )

    assert len(script.runs) == 1 and script.publishes == 0 and not script.posted
    assert statuses[-1].status == "closed" and "the smoke run finished in 3 turns" in statuses[-1].reason
    assert result.published is None
