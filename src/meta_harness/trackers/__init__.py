"""Issue trackers: the interface every one is used through (`base`), an adapter for each (`github`), and the
router that picks the adapter for a repo or a ref (`router`)."""

from meta_harness.trackers.base import (
    DONE,
    IN_PROGRESS,
    IN_REVIEW,
    STATUSES,
    TODO,
    Issue,
    Source,
    Tracker,
    TrackerError,
)
from meta_harness.trackers.github import GitHubTracker
from meta_harness.trackers.jira import JiraTracker
from meta_harness.trackers.router import Trackers

__all__ = [
    "DONE",
    "IN_PROGRESS",
    "IN_REVIEW",
    "STATUSES",
    "TODO",
    "GitHubTracker",
    "Issue",
    "JiraTracker",
    "Source",
    "Tracker",
    "TrackerError",
    "Trackers",
]


def connected() -> Trackers:
    """Every tracker the meta-harness supports, as one. Which of them a goal uses is the goal's choice, and one
    whose sign-in is missing says so when it is called."""
    from meta_harness.trackers import registry

    return Trackers([spec.build() for spec in registry.SUPPORTED.values() if spec.build])
