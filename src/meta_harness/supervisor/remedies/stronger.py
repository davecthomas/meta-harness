"""Remedies that give a stuck task to a stronger model, once, before an operator is asked.

retry_stronger  a task that failed on its own account (it could not finish the work) runs again with its
                route raised to the complex tier, so a stronger model and the careful harness profile try it
fix_again       a review its fix rounds did not settle goes through the review loop once more, its fix runs
                on the complex tier, with a fresh set of fix rounds

How many tries each makes is the task policy's `remedies` setting (0 turns it off), counted in the ledger.
Each is also one of the task's drives, so the Task API's own cap still holds.
"""

from meta_harness.config.kinds import remedy_tries
from meta_harness.run_service import routing
from meta_harness.supervisor import ledger
from meta_harness.supervisor.remedies.drive import Drive


class Stronger(Drive):
    action = ""
    complexity = "high"

    def of(self, task: dict) -> int | None:
        return remedy_tries(routing.task_policy(), self.id)

    def tries(self, task: dict) -> int:
        return ledger.tries(task["id"], self.id)


class RetryStronger(Stronger):
    id = "retry_stronger"
    action = "retry"
    did = "ran it again on the complex tier, with a stronger model"
    next = "if it fails again, I will ask you what to do"


class FixAgain(Stronger):
    id = "fix_again"
    action = "review"
    did = "sent it back through its review loop, its fixes on the complex tier"
    next = "if the review still has findings after these fix rounds, I will ask you"
