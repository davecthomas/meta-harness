"""record_prompt: the filled-in prompt each run of a task is given, recorded before the run starts.

The first run's prompt is repo-task-v1 (or the caller's own text) filled in by
prepare; a review or fix run's is review-v1 or fix-v1, filled in by
loop_prompt. Neither result is in an event, so when a run goes wrong an
operator could not tell a bad prompt (a missing check, a wrong path, an issue
body cut short) from a bad model. The RunTask workflow calls this activity
before each run of a Task API task, behind the `run-prompt` patch, and it
appends one `run_prompt` event on the task with the run's run_id, which the
console shows at the top of that run.
"""

from dataclasses import dataclass

from temporalio import activity

from meta_harness.run_service import events

# The template each review loop run's kind fills in, in prompts/.
LOOP_TEMPLATES = {"review": "review-v1", "fix": "fix-v1"}


def loop_template(kind: str) -> str:
    """The template a review loop run of this kind is given: review-v1 for a review, fix-v1 for any fix."""
    return LOOP_TEMPLATES["review"] if kind == "review" else LOOP_TEMPLATES["fix"]


@dataclass
class RunPromptRecord:
    """One run's prompt: the task and run it belongs to, the run's kind, the template's name and the text."""

    task_id: str
    run_id: str
    # task (the first run and its fallbacks), review or fix.
    kind: str
    template: str
    prompt: str


@activity.defn
async def record_prompt(record: RunPromptRecord) -> None:
    """Appends run_prompt for the run. Its event id makes a Temporal retry a no-op."""
    events.emit(
        "run_prompt",
        {"run_id": record.run_id, "kind": record.kind, "template": record.template, "prompt": record.prompt},
        task_id=record.task_id,
        run_id=record.run_id,
        event_id=events.stable_id(record.run_id, "run_prompt"),
    )
