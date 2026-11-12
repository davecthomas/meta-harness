"""A task an operator sent back to the queue picks up at its pull request instead of starting over.

The RunTask workflow runs against the review loop's scripted activities, with stand-ins for the task service
and for resume_state, which reads the pull request and the earlier runs. No model, gateway or GitHub is called.
"""

import json
import os

import pytest
from temporalio import activity
from temporalio.activity import _Definition
from temporalio.worker import Worker

from meta_harness.run_service.resume_activities import ResumeState, author_of
from meta_harness.run_service.shared import Attempt, PublishResult, RunTaskInput, TaskSpec, TaskStatusRequest
from meta_harness.run_service.workflows import RunTask

from .test_replay import HISTORIES, _task_activities
from .test_review_loop import AUTHOR, CHANGES, CLEAN, QUEUE, REPO, Script, env  # noqa: F401 - env is a fixture

PR = f"https://github.com/{REPO}/pull/7"
EARLIER = [
    Attempt(1, "standard", AUTHOR.label, "run-task-t-1:run-1", "finished", "ended FINISHED (completed)", "4.00"),
    Attempt(2, "review", "harness:pi-review/openai:sol", "run-task-t-1:run-2", "finished", "ended FINISHED", "0.25"),
    Attempt(3, "fix", AUTHOR.label, "run-task-t-1:run-3", "final", "ended ERROR (spend_limit)", "0.85"),
]


def _open(state: str = "open") -> ResumeState:
    published = PublishResult(branch="mh/t-1", base="main", commit="c5", files=[], pr_url=PR, created=False)
    return ResumeState(pr_state=state, published=published, author=AUTHOR, attempts=list(EARLIER))


async def _resume(env, script: Script, resume: str, state: ResumeState, goal_id: str = "", record: str = ""):  # noqa: F811
    statuses: list[TaskStatusRequest] = []

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
            resume=resume,
            retries=1,
        )

    @activity.defn(name="resume_state")
    async def resume_state(task_id: str) -> ResumeState:
        return state

    @activity.defn(name="set_task_status")
    async def set_task_status(request: TaskStatusRequest) -> str:
        statuses.append(request)
        return request.status

    base = [*script.activities(), *_task_activities(True, goal_id)]
    kept = [a for a in base if _Definition.from_callable(a).name not in ("load_task", "set_task_status")]
    activities = [*kept, load_task, resume_state, set_task_status]
    async with Worker(env.client, task_queue=QUEUE, workflows=[RunTask], activities=activities):
        handle = await env.client.start_workflow(
            RunTask.run, RunTaskInput(task="", repo="", task_id="t-1"), id=f"run-task-t-1-{resume}", task_queue=QUEUE
        )
        result = await handle.result()
        if record:
            # A resumed task's history, for test_replay to replay against later code (its docstring says why).
            history = await handle.fetch_history()
            (HISTORIES / f"{record}.json").write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n")
    return result, statuses


async def test_a_retried_task_with_an_open_pull_request_goes_back_into_the_review_loop(env):  # noqa: F811
    script = Script(verdicts=[CHANGES, CLEAN], fix_commits=["c6"])

    result, statuses = await _resume(env, script, "review", _open())

    assert result.outcome == "clean" and result.harness is None
    assert [r.prompt for r in script.runs] == ["review", "fix", "review"], "no new first run"
    assert script.waited_on[0] == "c5", "the loop starts at the pull request's head"
    assert [r.run_id.rsplit(":", 1)[1] for r in script.runs] == ["run-4", "run-5", "run-6"], (
        "numbered after the earlier runs"
    )
    assert script.runs[1].candidate.label == AUTHOR.label and script.runs[1].branch == "mh/t-1"
    assert statuses[0].reason.endswith("again (retry 1)")
    assert (statuses[-1].status, statuses[-1].pr_url) == ("ready", PR)


async def test_a_retried_task_whose_pull_request_has_merged_closes_without_a_run(env):  # noqa: F811
    script = Script()

    result, statuses = await _resume(env, script, "review", _open("merged"))

    assert result.outcome == "merged" and not script.runs
    assert statuses[-1].status == "closed" and "had merged" in statuses[-1].reason


async def test_a_retried_task_whose_pull_request_was_closed_starts_a_new_first_run_numbered_on(env):  # noqa: F811
    script = Script(verdicts=[CLEAN])

    result, _ = await _resume(env, script, "review", _open("closed"))

    assert result.outcome == "clean"
    assert [r.prompt for r in script.runs][1:] == ["review"] and script.runs[0].prompt != "review"
    assert script.runs[0].run_id.endswith(":run-4")


async def test_a_merge_an_operator_asked_for_merges_whatever_the_goal_says_then_waits_on_its_checks(env):  # noqa: F811
    script = Script(auto_merge=False)

    result, statuses = await _resume(env, script, "merge", _open(), goal_id="g-1")

    assert result.outcome == "merged" and not script.runs, "no review: the operator asked"
    assert not script.goals_asked, "the goal's auto_merge is not asked"
    assert [m.head_sha for m in script.merge_requests] == ["c5"]
    assert len(script.merge_checked) == 1 and statuses[-1].status == "closed"


async def test_a_merge_asked_for_on_a_head_that_moved_goes_through_checks_and_review_first(env):  # noqa: F811
    script = Script(verdicts=[CLEAN], auto_merge=True, merges=[("head_changed", "c9"), "merged"])

    result, _ = await _resume(env, script, "merge", _open(), goal_id="g-1")

    assert result.outcome == "merged"
    assert script.waited_on == ["c9"] and [p.head_sha for p in script.posted] == ["c9"]


async def test_a_merge_asked_for_that_conflicts_resolves_the_conflict_before_any_review(env):  # noqa: F811
    # #258: the supervisor asked for the merge of a pull request with a clean review that had fallen behind
    # its base. Reviewed first, it met new findings each round and never reached the merge's conflict fix.
    # The scripted publish returns "c0" first, here the fix run's commit.
    script = Script(verdicts=[CLEAN], auto_merge=True, merges=["conflict", "merged"])

    result, _ = await _resume(env, script, "merge", _open(), goal_id="g-1")

    assert result.outcome == "merged"
    assert [r.prompt for r in script.runs] == ["fix", "review"], "the conflict is fixed, then the result reviewed"
    assert script.runs[0].merge_base == "main", "the host merges the base into the fix run's clone"
    assert next(p for p in script.prompts if p.kind == "fix").conflict is True
    assert script.waited_on == ["c0"] and [m.head_sha for m in script.merge_requests] == ["c5", "c0"]


@pytest.mark.skipif(not os.environ.get("MH_RECORD_HISTORIES"), reason="records histories only when asked")
async def test_record_the_conflict_first_history(env):  # noqa: F811
    # After the conflict-first patch: a merge asked for that conflicts starts with the fix run that resolves it.
    if (HISTORIES / "patched-conflict-first.json").exists():
        pytest.skip("recorded already: an old history is what a deploy finds running, so it stays as it is")
    script = Script(verdicts=[CLEAN], auto_merge=True, merges=["conflict", "merged"])
    await _resume(env, script, "merge", _open(), goal_id="g-1", record="patched-conflict-first")


def test_the_author_is_the_pair_of_the_last_finished_run_that_was_not_a_review_or_fix(monkeypatch):
    from meta_harness.run_service import routing

    monkeypatch.setattr(routing, "replace_engine", lambda pair: pair)
    monkeypatch.setattr(routing, "pinned", lambda pair: f"candidate for {pair}")
    assert author_of(EARLIER) == f"candidate for {AUTHOR.label}"
    assert author_of([a for a in EARLIER if a.tier != "standard"]) is None
