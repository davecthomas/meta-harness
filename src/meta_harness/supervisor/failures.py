"""Whether a task's failure was the task's or the system's, from the reason its workflow recorded.

A task fails for one of two kinds of reason. The work could not be done: the run changed nothing, hit its
turn limit, a setting is wrong, the review never came clean. Or the system let it down while the work was
fine: a worker restarted under it, a sandbox did not start, every model was unavailable, the code host did
not answer. The first kind needs a person. The second needs only another try, and nothing used to give it
one: a task that failed in an outage stayed failed after the outage ended.

`kind` reads the recorded reason and answers transient or permanent. It answers transient only for the
errors named here, each one a failure of the control plane or of something it depends on; anything it does
not recognize is permanent, so an unknown failure waits for a person rather than being run again and again.
"""

import re

TRANSIENT = "transient"
PERMANENT = "permanent"

# Each names a way the system, not the task, failed. They are the error types and end reasons the run service
# records (run_service/workflows.py, activities.py), and the words of the code host's and Temporal's errors.
_TRANSIENT = re.compile(
    "|".join(
        [
            # The worker stopped under the run: a deploy restarts it on every merge.
            r"WorkerShutdown",
            r"worker shutdown",
            # The per-run sandbox, the harness server in it, or the HTTP Gateway did not answer.
            r"SandboxUnavailable",
            r"HarnessUnavailable",
            r"GatewayUnavailable",
            r"PlannerUnavailable",
            r"BudgetUnavailable",
            # A run that ended on the model provider's, the harness's or the server's error.
            r"\((?:model_error|harness_error|server_shutdown)\)",
            r"every candidate failed",
            r"none of the \d+ attempts finished",
            # An activity that timed out or lost its heartbeat, which a restart or an outage does.
            r"TimeoutError",
            r"heartbeat timeout",
            r"activity (?:task )?timed out",
            # The code host or another service answering 5xx, limiting the rate, or not answering.
            r"answered 5\d\d",
            r"\b50[0234]\b.*(?:GitHub|Gateway|code host)",
            r"(?:GitHub|code host).*\b50[0234]\b",
            r"rate limit",
            r"is unreachable",
            r"Connection (?:refused|reset)",
            r"timed out",
        ]
    ),
    re.IGNORECASE,
)
# A reason that names one of these is the task's own, whatever else it says: retrying spends and changes nothing.
_PERMANENT = re.compile(
    "|".join(
        [
            r"HarnessConfigError",
            r"spend limit",
            r"\((?:max_turns|turn_limit|max_minutes|time_limit|spend_limit)\)",
            r"pi changed nothing",
            r"fix rounds",
            r"keep ending without finishing",
        ]
    ),
    re.IGNORECASE,
)


def kind(reason: str | None) -> str:
    """transient when the recorded reason names a failure of the system, else permanent."""
    text = reason or ""
    if not text or _PERMANENT.search(text):
        return PERMANENT
    return TRANSIENT if _TRANSIENT.search(text) else PERMANENT
