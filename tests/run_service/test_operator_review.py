"""An operator takes a pull request's review over: the round waits for their verdict, which decides it.

The RunTask workflow runs against the review loop's scripted activities, with stand-ins for the task service
and the hold. No model, gateway or GitHub is called.
"""

import asyncio
import json

from temporalio import activity
from temporalio.activity import _Definition
from temporalio.worker import Worker

from meta_harness.run_service.budget_activities import BudgetHold
from meta_harness.run_service.shared import OperatorMessage, RunTaskInput
from meta_harness.run_service.workflows import RunTask

from .test_replay import _task_activities
from .test_review_hold import TWO
from .test_review_loop import CLEAN, QUEUE, Script, env  # noqa: F401 - env is a fixture


def _word(text: str, n: int = 1) -> OperatorMessage:
    return OperatorMessage(message_id=n, text=text, when="review")


async def _claimed(env, script: Script, name: str, answer: str, goal_id: str = ""):  # noqa: F811
    """Runs a task whose review an operator claimed before it began, and answers once the round waits."""
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
            RunTask.run,
            RunTaskInput(task="", repo="", task_id=f"t-{name}"),
            id=f"run-task-t-{name}",
            task_queue=QUEUE,
            # With the start, so the claim is in before the first review.
            start_signal="operator_message",
            start_signal_args=[_word("claim:dave")],
        )
        await asyncio.wait_for(waiting.wait(), timeout=30)
        await handle.signal(RunTask.operator_message, _word(answer, 2))
        result = await handle.result()
    return result, holds


async def test_the_operators_verdict_decides_the_round_and_the_fix_run_gets_their_findings(env):  # noqa: F811
    script = Script(verdicts=[TWO, CLEAN], fix_commits=["c1"])
    own = {"path": "app.py", "line": 12, "body": "This loop never ends on an empty list."}

    result, holds = await _claimed(env, script, "verdict", "verdict:" + json.dumps({"keep": [2], "findings": [own]}))

    assert result.outcome == "clean"
    assert holds[0].waiting and holds[0].on == "operator_review"
    assert "dave took the review over. The agent's review found 2 findings" in holds[0].reason
    assert not holds[1].waiting and "dave reviewed it; 2 findings go to the fix run" in holds[1].reason
    fix = next(p for p in script.prompts if p.kind == "fix")
    assert [f["body"] for f in fix.findings] == ["Off by one.", "This loop never ends on an empty list."]
    [comment] = script.comments
    assert "**Review by dave, round 1**: changes needed." in comment.body
    assert "Of the agent's 2 findings, dave kept 1." in comment.body
    assert "- app.py:12: This loop never ends on an empty list." in comment.body
    # The claim was for one round: the second review decides alone.
    assert len(holds) == 2


async def test_an_operator_who_finds_nothing_to_change_makes_the_round_clean(env):  # noqa: F811
    script = Script(verdicts=[TWO], auto_merge=True)

    result, _ = await _claimed(env, script, "clean", 'verdict:{"keep": [], "findings": []}', goal_id="g-1")

    assert result.outcome == "merged" and not [p for p in script.prompts if p.kind == "fix"]
    assert "dave reviewed round 1 and asked for no change" in result.outcome_reason


async def test_an_operator_can_ask_for_changes_the_agent_did_not(env):  # noqa: F811
    script = Script(verdicts=[CLEAN, CLEAN], fix_commits=["c1"])
    own = {"body": "The error message names the wrong file."}

    result, _ = await _claimed(env, script, "added", "verdict:" + json.dumps({"keep": [], "findings": [own]}))

    assert result.outcome == "clean"
    fix = next(p for p in script.prompts if p.kind == "fix")
    assert [f["body"] for f in fix.findings] == ["The error message names the wrong file."]


async def test_a_released_review_decides_on_the_agents_verdict(env):  # noqa: F811
    script = Script(verdicts=[TWO, CLEAN], fix_commits=["c1"])

    result, holds = await _claimed(env, script, "release", "release")

    assert result.outcome == "clean" and not script.comments
    assert len(next(p for p in script.prompts if p.kind == "fix").findings) == 2
    assert "dave gave the review back, so the agent's verdict decides" in holds[1].reason
