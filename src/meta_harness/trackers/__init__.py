"""Issue trackers: the interface every one is used through (`base`), an adapter for each (`github`), and the
router that picks the adapter for a repo or a ref (`router`)."""

from meta_harness.trackers.base import DONE, IN_PROGRESS, IN_REVIEW, STATUSES, TODO, Issue, Tracker, TrackerError
from meta_harness.trackers.github import GitHubTracker
from meta_harness.trackers.router import Trackers

__all__ = [
    "DONE",
    "IN_PROGRESS",
    "IN_REVIEW",
    "STATUSES",
    "TODO",
    "GitHubTracker",
    "Issue",
    "Tracker",
    "TrackerError",
    "Trackers",
]


def connected() -> Trackers:
    """Every tracker this deployment is connected to."""
    return Trackers([GitHubTracker()])
