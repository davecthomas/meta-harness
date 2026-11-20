"""record_prompt: the filled-in prompt each run of a task is given, recorded before the run starts.

The first run's prompt is repo-task-v1 (or the caller's own text) filled in by
prepare (plan-v1 for a goal's planning task); a review or fix run's is review-v4 or fix-v1, filled in by
loop_prompt. Neither result is in an event, so when a run goes wrong an
operator could not tell a bad prompt (a missing check, a wrong path, an issue
body cut short) from a bad model. The RunTask workflow calls this activity
before each run of a Task API task, behind the `run-prompt` patch, and it
appends one `run_prompt` event on the task with the run's run_id, which the
console shows at the top of that run.

When the workflow gives the run's provenance (run_service/provenance.py), the
activity appends it too, as `run_provenance`, the same way and just before the
prompt.
"""

from dataclasses import dataclass
from typing import Any

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from meta_harness.run_service import events, provenance

# The template each review loop run's kind fills in, in prompts/.
LOOP_TEMPLATES = {"review": "review-v4", "fix": "fix-v1"}


def loop_template(kind: str) -> str:
    """The template a review loop run of this kind is given: review-v4 for a review, fix-v1 for any fix."""
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
    # Where the run came from, built by the workflow. None from a workflow that predates provenance.
    provenance: dict[str, Any] | None = None


@activity.defn
async def record_prompt(record: RunPromptRecord) -> None:
    """Appends run_prompt for the run, and returns only once the event service has stored it.

    It appends at once rather than through the outbox, so the prompt is stored, and holds an earlier
    position than any of the run's own events, before run_harness starts, on this worker or another.
    An unreachable event service raises, so Temporal retries; the stable event id makes a retry that
    follows a stored append a no-op. A rejected event (422) is a bug a retry cannot fix.

    A provenance record that does not hold together is logged as an error and not appended: the run still
    starts, and no event points somewhere false.
    """
    if record.provenance:
        _append_provenance(record, record.provenance)
    _append(
        "run_prompt",
        {"run_id": record.run_id, "kind": record.kind, "template": record.template, "prompt": record.prompt},
        record,
        events.stable_id(record.run_id, "run_prompt"),
    )


def _append(event_type: str, payload: dict[str, Any], record: RunPromptRecord, event_id: str) -> None:
    try:
        events.append(event_type, payload, task_id=record.task_id, run_id=record.run_id, event_id=event_id)
    except httpx.HTTPStatusError as err:
        if err.response.status_code == 422:
            raise ApplicationError(
                f"the event service refused {event_type}: {err.response.text}", non_retryable=True
            ) from err
        raise


def _append_provenance(record: RunPromptRecord, made: dict[str, Any]) -> None:
    try:
        provenance.check(made, record.task_id)
    except provenance.ProvenanceError as err:
        activity.logger.error("task %s run %s: its provenance is not recorded: %s", record.task_id, record.run_id, err)
        return
    if made["run_id"] != record.run_id:
        activity.logger.error(
            "task %s run %s: its provenance names run %s", record.task_id, record.run_id, made["run_id"]
        )
        return
    # A task sent back with no pull request numbers its runs from 1 again, so the workflow is in the event id.
    _append(
        provenance.EVENT_TYPE, made, record, events.stable_id(record.run_id, provenance.EVENT_TYPE, made["workflow"])
    )
