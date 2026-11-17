"""What every issue tracker the meta-harness works with can do, whichever system holds the issues.

A tracker is where a goal's issues live: the planner reads issues from it, creates a plan's or a split's issues
in it, and tells it how each issue's task is going. An adapter is one tracker's implementation of `Tracker`.
The code host, where branches and pull requests live, is a separate thing and is not behind this interface.

An issue's ref names its tracker by its first part, the scheme: `github:<owner>/<repo>#<number>`. Everything
outside an adapter treats the rest of a ref as opaque: it is stored, compared and passed back, never parsed.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

# Where an issue stands, in the words every tracker's adapter maps to its own: an issue no one has started,
# one whose task is running, one whose pull request is open for review, and one that is done.
TODO, IN_PROGRESS, IN_REVIEW, DONE = "todo", "in_progress", "in_review", "done"
STATUSES = (TODO, IN_PROGRESS, IN_REVIEW, DONE)


class TrackerError(RuntimeError):
    """A tracker call failed. `retryable` is False when repeating it would fail the same way; `status` is the
    tracker's HTTP status when it answered with one."""

    def __init__(self, message: str, retryable: bool = True, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


@dataclass
class Issue:
    ref: str
    # The code repo the issue's work is done in, as owner/name.
    repo: str
    number: int
    title: str
    body: str
    labels: list[str]
    state: str
    depends_on: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    updated_at: str = ""
    # The body's `Paths:` lines: the areas the task locks, so issues naming one area run in turn.
    paths: list[str] = field(default_factory=list)
    # Who opened the issue and what they are to the repo, as the tracker says (GitHub: OWNER, MEMBER,
    # COLLABORATOR, CONTRIBUTOR, NONE and so on). Empty when the tracker does not say.
    author: str = ""
    author_association: str = ""


class Tracker(Protocol):
    """One issue tracker. `repo` is always the code repo, owner/name: an adapter whose issues live elsewhere
    (a project in another system) maps the repo to that place itself."""

    # The first part of every ref this tracker's issues have.
    scheme: str
    # Whether the planner tells the tracker how an issue's task is going (planner/tracker_progress.py).
    writes_progress: bool

    async def list_open(self, repo: str, label: str | None) -> list[Issue]:
        """The repo's open issues carrying the label; every open issue when label is None."""
        ...

    async def read(self, ref: str) -> Issue:
        """One issue as it is now, open or closed."""
        ...

    async def ensure_label(self, repo: str, label: str) -> None:
        """Makes sure an issue created for the repo can carry the label."""
        ...

    async def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> Issue:
        """Creates one issue for the repo, carrying the labels."""
        ...

    async def comment(self, ref: str, body: str) -> str:
        """Adds a comment to the issue. Returns the comment's address, or "" when the tracker gives none."""
        ...

    async def set_status(self, ref: str, status: str) -> bool:
        """Moves the issue to one of STATUSES, as far as the tracker has such a state. Returns whether the
        issue changed: False when it was there already or the tracker has no state for it."""
        ...

    async def link_pull_request(self, ref: str, pr_url: str, title: str) -> None:
        """Records the pull request on the issue, where the tracker does not find it by itself."""
        ...

    def url(self, ref: str) -> str | None:
        """Where a person reads the issue, or None for a ref that is not this tracker's."""
        ...

    def repo_of(self, ref: str) -> str:
        """The code repo the issue's work is done in, owner/name.

        Raises:
            ValueError: The ref is not one of this tracker's.
            TrackerError: The tracker cannot say which repo the issue belongs to.
        """
        ...


def scheme_of(ref: str) -> str:
    """The tracker a ref names: its part before the first colon. "" for a ref with none."""
    scheme, colon, _ = ref.partition(":")
    return scheme if colon else ""


@dataclass(frozen=True)
class Setting:
    """One setting of a tracker, as the console's form shows it."""

    name: str
    label: str
    # text, or projects: a list of {"key": <project key>, "repo": <owner/name>}; statuses: a name for each status.
    kind: str = "text"
    required: bool = False
    help: str = ""


@dataclass(frozen=True)
class Supported:
    """One tracker the meta-harness supports, as its adapter module describes it (the module's SPEC). The
    registry is the list of these: a new tracker is one module and one line there."""

    scheme: str
    title: str
    description: str
    settings: list[Setting] = field(default_factory=list)
    # The environment variables its sign-in comes from, which are secrets and so not in the configuration.
    credentials: list[str] = field(default_factory=list)
    # Why settings cannot be used, given the registered repos' names in lower case (None: not known); empty
    # when they can.
    check: Callable[[dict[str, Any], set[str] | None], list[str]] = lambda settings, repos: []
    # Builds the adapter.
    build: Callable[[], "Tracker"] | None = None
    # Whether the planner tells the tracker how an issue's task is going (comments, status, the pull request's
    # link). False for a tracker that shows all that by itself, as GitHub does for an issue its pull request names.
    writes_progress: bool = False
