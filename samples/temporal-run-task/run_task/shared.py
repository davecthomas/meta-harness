"""Names and payload types shared by the workflow, the activities, the worker and the starter.

Payloads are dataclasses. The SDK's default data converter turns them into JSON
and back, using the type hints on the workflow and activity signatures.
"""

import os
from dataclasses import dataclass

TASK_QUEUE = "run-task"
TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
TEMPORAL_NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "default")


@dataclass
class RunTaskInput:
    """What the starter asks for. Secrets stay out: workflow history is visible in the web UI."""

    template: str
    engine: str
    model: str


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


@dataclass
class HarnessResult:
    """What one pi run reported, parsed from its final lines."""

    attempt: int
    exit_code: int
    turns: int
    failed_turns: int
    tokens_in: int
    tokens_out: int
    usd: float
    file_content: str | None


@dataclass
class RunTaskResult:
    template: str
    prompt: str
    harness: HarnessResult
