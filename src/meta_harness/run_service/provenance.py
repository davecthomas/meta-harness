"""A run's provenance: where each run of a task came from, as the control plane knows it.

The RunTask workflow builds one record per run of a Task API task, from what it holds itself: the task it
loaded from the task service (its goal, its issue, its parent task, its type, how often an operator sent it
back), the run's number, kind and candidate, the template its prompt was filled from, and the run it follows.
Nothing in it comes from the run's own output, so an agent cannot write its own provenance.

The record travels with the run:

- record_prompt appends it as `run_provenance` before the run starts, beside the run's `run_prompt`.
- publish writes it into the pull request's description and, as trailers, into the commit message, so the
  code host holds which task, run and goal a commit came from.

`check` refuses a record whose run is not one of its task's runs, or whose earlier run is not an earlier run
of the same task: such a record is a bug, and an event with it would point somewhere false.

Pure functions only: the workflow calls `build` inside the SDK's sandbox.
"""

import re
from dataclasses import asdict, dataclass
from typing import Any

EVENT_TYPE = "run_provenance"
KINDS = ("task", "review", "fix")
_RUN = re.compile(r"^(?P<workflow>.+):run-(?P<attempt>\d+)$")


class ProvenanceError(ValueError):
    """A provenance record that does not hold together. The message says why."""


@dataclass
class TaskOrigin:
    """What a task came from, read once from the task service when its RunTask workflow starts."""

    task_id: str
    goal_id: str = ""
    # The task's external_ref: the tracker issue it works on, such as github:owner/name#12.
    issue: str = ""
    parent_task_id: str = ""
    task_type: str = ""
    repo: str = ""
    # How many times an operator or the supervisor sent the task back: 0 in its first RunTask workflow.
    retries: int = 0


@dataclass
class RunProvenance:
    task_id: str
    run_id: str
    attempt: int
    # task (the task's own run and its fallbacks), review or fix.
    kind: str
    candidate: str
    template: str
    # The router tier a task run was routed to; None for a pinned run, and for a review or fix run.
    tier: str | None
    # The task's run just before this one, which this run follows on from; None for the task's first run, and
    # for each of several attempts made at once.
    follows: str | None
    # The task's RunTask workflow this run is in: 1 for the first, 2 after one retry, and on.
    workflow: int
    goal_id: str | None
    issue: str | None
    parent_task_id: str | None
    task_type: str | None
    repo: str | None


def attempt_of(run_id: str) -> int | None:
    """The run's number within its task, from its run id (<workflow id>:run-<n>), or None for another id."""
    match = _RUN.match(run_id)
    return int(match["attempt"]) if match else None


def build(
    origin: TaskOrigin,
    run_id: str,
    kind: str,
    candidate: str,
    template: str,
    tier: str | None = None,
    follows: str = "",
) -> dict[str, Any]:
    """The run's provenance record, as the `run_provenance` event's payload."""
    attempt = attempt_of(run_id)
    record = RunProvenance(
        task_id=origin.task_id,
        run_id=run_id,
        attempt=attempt or 0,
        kind=kind,
        candidate=candidate,
        template=template,
        tier=tier if kind == "task" else None,
        follows=follows or None,
        workflow=origin.retries + 1,
        goal_id=origin.goal_id or None,
        issue=origin.issue or None,
        parent_task_id=origin.parent_task_id or None,
        task_type=origin.task_type or None,
        repo=origin.repo or None,
    )
    return asdict(record)


def check(record: dict[str, Any], task_id: str) -> None:
    """Raises ProvenanceError when the record is not one the control plane could have made for this task's run."""
    if record.get("task_id") != task_id:
        raise ProvenanceError(f"the record is for task {record.get('task_id')!r}, not {task_id!r}")
    if record.get("kind") not in KINDS:
        raise ProvenanceError(f"a run's kind is one of {', '.join(KINDS)}, not {record.get('kind')!r}")
    run_id = str(record.get("run_id") or "")
    match = _RUN.match(run_id)
    if not match or not _of_task(match["workflow"], task_id):
        raise ProvenanceError(f"{run_id!r} is not a run of task {task_id}")
    if int(match["attempt"]) != record.get("attempt"):
        raise ProvenanceError(f"{run_id} is run {match['attempt']}, not run {record.get('attempt')}")
    if not str(record.get("candidate") or "").strip():
        raise ProvenanceError(f"{run_id} names no candidate")
    if not isinstance(record.get("workflow"), int) or record["workflow"] < 1:
        raise ProvenanceError(f"{run_id}'s workflow is numbered from 1, not {record.get('workflow')!r}")
    follows = record.get("follows")
    if follows is None:
        return
    earlier = _RUN.match(str(follows))
    # A retry may start the task's next workflow under its other id (run-task-<id> then <id>, or back), so the
    # earlier run belongs to the task when its workflow id is either form of the task's id.
    if not earlier or not _of_task(earlier["workflow"], task_id):
        raise ProvenanceError(f"{run_id} follows {follows!r}, which is not a run of the same task")
    if int(earlier["attempt"]) >= int(match["attempt"]):
        raise ProvenanceError(f"{run_id} follows {follows}, which is not an earlier run")


def _of_task(workflow_id: str, task_id: str) -> bool:
    # A Task API task's workflow id is its task id; a workflow started by hand is run-task-<REF>.
    return workflow_id in (task_id, f"run-task-{task_id}")


def pr_lines(record: dict[str, Any] | None) -> list[str]:
    """The lines the pull request's description gives the run's provenance in, under its Run heading."""
    if not record:
        return []
    lines = [f"- Task: `{record['task_id']}`"]
    if record.get("goal_id"):
        lines.append(f"- Goal: `{record['goal_id']}`")
    if record.get("issue"):
        lines.append(f"- Issue: `{record['issue']}`")
    if record.get("parent_task_id"):
        lines.append(f"- Parent task: `{record['parent_task_id']}`")
    if record.get("follows"):
        lines.append(f"- Follows run: `{record['follows']}`")
    back = int(record.get("workflow") or 1) - 1
    if back:
        lines.append(f"- Workflow: {back + 1} (the task was sent back {'once' if back == 1 else f'{back} times'})")
    return lines


def trailers(record: dict[str, Any] | None) -> list[str]:
    """Git trailers for the commit the run's changes are published in, so the code host keeps where it came from."""
    if not record:
        return []
    lines = [f"Meta-Harness-Task: {record['task_id']}", f"Meta-Harness-Run: {record['run_id']}"]
    if record.get("goal_id"):
        lines.append(f"Meta-Harness-Goal: {record['goal_id']}")
    if record.get("issue"):
        lines.append(f"Meta-Harness-Issue: {record['issue']}")
    return lines
