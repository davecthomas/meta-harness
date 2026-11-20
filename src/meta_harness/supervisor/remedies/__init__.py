"""Remedies: what the supervisor does for a stuck task, one small module each.

The supervisor finds where a task stands (rules.decide). When no one else will move it, the decision names
a situation and the remedy for it, deterministically: the same facts always give the same remedy. This
package carries the remedy out. Each remedy is a class in its own module, listed in `registry`, with:

    id          the name rules.decide gives it
    of(task)    the most tries it makes before an operator is asked, or None for no limit
    tries(task) how many it has made
    apply(ctx)  does it, through the Task API or the planner, and returns an Outcome

Every try is written to the ledger (`ledger.py`): a `supervisor.remedy` row and a `remedy_applied` event,
both the same fixed record, so a page, the planner's update or a model reads what was done without reading
the raw events:

    {"situation": "...", "remedy": "...", "try": 1, "of": 3, "outcome": "started",
     "because": "why it was needed", "did": "what was done", "next": "what happens next"}

To add a remedy: write its module, list it in `registry.REMEDIES`, and have rules.decide name it for its
situation. tests/supervisor walks every remedy decide can name and fails one with no module.
"""

from dataclasses import dataclass
from typing import Any

# How a try ended. started: the task was sent on its way (a workflow or the queue now has it). done: the
# remedy finished the job itself (the task was ended or canceled). failed: the remedy could not be applied;
# `error` says why, and the supervisor tries again later.
OUTCOMES = ("started", "done", "failed")


@dataclass(frozen=True)
class Outcome:
    outcome: str
    did: str
    next: str


@dataclass
class Context:
    """What a remedy works with: the task, its goal, its pull request as read, the decision that named the
    remedy, and the supervisor's clients."""

    task: dict
    goal: dict | None
    pull: Any
    decision: Any
    supervisor: Any


class Remedy:
    """A remedy's defaults: no limit on tries, and none counted."""

    id = ""

    def of(self, task: dict) -> int | None:
        return None

    def tries(self, task: dict) -> int:
        return 0

    async def apply(self, ctx: Context) -> Outcome | None:
        """Carry the remedy out. None: nothing was done this pass, so nothing is recorded."""
        raise NotImplementedError
