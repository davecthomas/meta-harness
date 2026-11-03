"""Names and payload types shared by the workflow, the activities, the worker and the starter.

Payloads are dataclasses. The SDK's default data converter turns them into JSON
and back, using the type hints on the workflow and activity signatures.
"""

import os
from dataclasses import dataclass

TASK_QUEUE = "run-task"
TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
TEMPORAL_NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "default")
DEFAULT_TEMPLATE = "hello-v1"
# The template name a run reports when the caller gave the task text itself.
INLINE_TEMPLATE = "inline"


def workflow_id_for(ref: str) -> str:
    """The caller's task reference becomes the workflow id, so a REF names one task."""
    return f"run-task-{ref}"


@dataclass
class RunTaskInput:
    """What the starter asks for. Secrets stay out: workflow history is visible in the web UI."""

    engine: str
    model: str
    # The harness server that runs this task. Each task has its own until the pool manager exists.
    harness_url: str
    # The task text. When empty, prepare reads prompts/<template>.txt instead.
    task: str = ""
    template: str = DEFAULT_TEMPLATE


@dataclass
class PreparedRun:
    """The prepare activity's output: the prompt pi will run."""

    template: str
    prompt: str


@dataclass
class HarnessRequest:
    prompt: str
    engine: str
    model: str
    harness_url: str


@dataclass
class HarnessResult:
    """What the harness server reported for the run: its run_finished event and GET /usage."""

    attempt: int
    run_id: str
    status: str
    end_reason: str
    reattached: bool
    turns: int
    failed_calls: int
    tokens_in: int
    tokens_out: int
    usd: float
    files_changed: list[str]
    answer: str | None


@dataclass
class RunTaskResult:
    template: str
    prompt: str
    harness: HarnessResult
