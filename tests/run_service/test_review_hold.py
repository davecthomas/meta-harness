"""A review that asks for changes waits for an operator, who may dismiss findings before the fix run starts.

The RunTask workflow runs against the review loop's scripted activities, with stand-ins for the task service
and the hold. No model, gateway or GitHub is called.
"""

import asyncio

from temporalio import activity
from temporalio.activity import _Definition
from temporalio.worker import Worker

from meta_harness.run_service.budget_activities import BudgetHold
from meta_harness.run_service.shared import OperatorMessage, RunTaskInput
from meta_harness.run_service.workflows import RunTask

from .test_replay import _task_activities
from .test_review_loop import CLEAN, QUEUE, Script, env  # noqa: F401 - env is a fixture

TWO = {
    "verdict": "changes",
    "summary": "Two things.",
    "findings": [
        {"path": "pyproject.toml", "line": 3, "body": "Revert the version bump."},
        {"path": "app.py", "line": 9, "body": "Off by one."},
    ],
}


async def _held(env, script: Script, name: str, words: list[str], goal_id: str = ""):  # noqa: F811
    holds: list[BudgetHold] = []
    waiting = asyncio.Event()

    @activity.defn(name="budget_hold")
    async def budget_hold(hold: BudgetHold) -> None:
        holds.append(hold)
        if hold.waiting:
            waiting.set()

    base = [*script.activities(), *_task_activities(True, goal_id)]
    kept = [a for a in base if _Definition.from_callable(a).name != "budget_hold"]
    async with Worker(env.client, task_queue=QUEUE, workflows=[RunTask], activities=[*kept, budget_hold]):
        handle = await env.client.start_workflow(
            RunTask.run, RunTaskInput(task="", repo="", task_id=f"t-{name}"), id=f"run-task-t-{name}", task_queue=QUEUE
        )
        if words:
            await asyncio.wait_for(waiting.wait(), timeout=30)
            for n, word in enumerate(words, 1):
                await handle.signal(RunTask.operator_message, OperatorMessage(message_id=n, text=word, when="review"))
        result = await handle.result()
    return result, holds


async def test_a_dismissed_finding_is_left_out_of_the_fix_run_and_posted_on_the_pull_request(env):  # noqa: F811
    script = Script(verdicts=[TWO, CLEAN], fix_commits=["c1"], hold_minutes=30)

    result, holds = await _held(env, script, "dismiss", ["dismiss:1:1:AGENTS.md requires the bump.", "proceed:1"])

    assert result.outcome == "clean"
    fix = next(p for p in script.prompts if p.kind == "fix")
    assert [f["body"] for f in fix.findings] == ["Off by one."], "the dismissed finding is not asked for"
    second_review = [p for p in script.prompts if p.kind == "review"][1]
    assert [f["body"] for f in second_review.previous_findings] == ["Off by one."]
    assert holds[0].waiting and holds[0].on == "review"
    assert "review round 1 asked for changes (2 findings); waiting 30 minutes" in holds[0].reason
    assert not holds[1].waiting and "1 of 2 findings go to the fix run" in holds[1].reason
    [comment] = script.comments
    assert "an operator dismissed 1 of 2 findings" in comment.body
    assert "- Finding 1 (pyproject.toml): dismissed. AGENTS.md requires the bump." in comment.body


async def test_with_every_finding_dismissed_the_round_counts_as_clean_and_no_fix_run_starts(env):  # noqa: F811
    script = Script(verdicts=[TWO], auto_merge=True, hold_minutes=30)

    result, holds = await _held(
        env, script, "all", ["dismiss:1:1:Wrong.", "dismiss:1:2:Also wrong.", "proceed:1"], goal_id="g-1"
    )

    assert result.outcome == "merged" and not [p for p in script.prompts if p.kind == "fix"]
    assert "an operator dismissed every finding of review round 1" in result.outcome_reason
    assert "every finding was dismissed, so the round counts as clean" in holds[-1].reason


async def test_a_held_review_no_one_answers_goes_to_its_fix_run_with_every_finding(env):  # noqa: F811
    script = Script(verdicts=[TWO, CLEAN], fix_commits=["c1"], hold_minutes=30)

    result, holds = await _held(env, script, "timeout", [])

    assert result.outcome == "clean" and not script.comments
    fix = next(p for p in script.prompts if p.kind == "fix")
    assert len(fix.findings) == 2
    assert "2 of 2 findings go to the fix run" in holds[-1].reason


async def test_words_for_another_round_or_no_such_finding_change_nothing(env):  # noqa: F811
    script = Script(verdicts=[TWO, CLEAN], fix_commits=["c1"], hold_minutes=30)

    await _held(env, script, "stray", ["dismiss:2:1:Wrong round.", "dismiss:1:9:No such finding.", "proceed:1"])

    assert len(next(p for p in script.prompts if p.kind == "fix").findings) == 2 and not script.comments


async def test_a_policy_with_no_hold_sends_findings_straight_to_the_fix_run(env):  # noqa: F811
    script = Script(verdicts=[TWO, CLEAN], fix_commits=["c1"])

    result, holds = await _held(env, script, "nohold", [])

    assert result.outcome == "clean" and holds == []
