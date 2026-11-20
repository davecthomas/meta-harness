"""Every remedy, by the id rules.decide names it with. Adding a remedy is a new module and a line here."""

from meta_harness.supervisor.remedies import Remedy
from meta_harness.supervisor.remedies.cancel_for_goal import CancelForGoal
from meta_harness.supervisor.remedies.drive import Merge, Retry, Review
from meta_harness.supervisor.remedies.end_at_pull_request import EndAtPullRequest
from meta_harness.supervisor.remedies.raise_limit import RaiseLimit
from meta_harness.supervisor.remedies.recover import Recover
from meta_harness.supervisor.remedies.stronger import FixAgain, RetryStronger
from meta_harness.supervisor.remedies.wait import Wait

REMEDIES: dict[str, Remedy] = {
    remedy.id: remedy
    for remedy in (
        Recover(),
        EndAtPullRequest(),
        Merge(),
        Review(),
        Retry(),
        CancelForGoal(),
        Wait(),
        RetryStronger(),
        FixAgain(),
        RaiseLimit(),
    )
}


def get(remedy_id: str) -> Remedy:
    """The remedy named, or ValueError naming the ones there are."""
    try:
        return REMEDIES[remedy_id]
    except KeyError:
        raise ValueError(f"no remedy {remedy_id!r}; the remedies are {', '.join(sorted(REMEDIES))}") from None
