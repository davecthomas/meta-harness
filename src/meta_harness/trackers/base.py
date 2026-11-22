"""What every issue tracker the meta-harness works with can do, whichever system holds the issues.

A tracker is where a goal's issues live: the planner reads issues from it, creates a plan's or a split's issues
in it, and tells it how each issue's task is going. An adapter is one tracker's implementation of `Tracker`.
The code host, where branches and pull requests live, is a separate thing and is not behind this interface.

An issue's ref names its tracker by its first part, the scheme: `github:<owner>/<repo>#<number>`. Everything
outside an adapter treats the rest of a ref as opaque: it is stored, compared and passed back, never parsed.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

# Where an issue stands, in the words every tracker's adapter maps to its own: an issue no one has started,
# one whose task is running, one whose pull request is open for review, and one that is done.
TODO, IN_PROGRESS, IN_REVIEW, DONE = "todo", "in_progress", "in_review", "done"
STATUSES = (TODO, IN_PROGRESS, IN_REVIEW, DONE)


# The label every issue the meta-harness creates or changes carries, so a person in the tracker can tell its
# work from anyone else's and list it. A deployment may name another (the trackers configuration's `label`).
MARK_LABEL = "ai:meta-harness"


def configured_label() -> str:
    """The deployment's label for issues the meta-harness created or changed."""
    from meta_harness.trackers import registry

    return registry.mark_label()


class Marked:
    """The refs an adapter has labeled since the service started, so each issue is labeled with one call and
    not one for every later write. Shared by an adapter and the copies of it bound to goals."""

    def __init__(self) -> None:
        # Each with the label it was given, so a changed label is added too.
        self.refs: set[tuple[str, str]] = set()


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
    # The ref of the issue this one sits under in the tracker (Jira: its parent, such as an epic). None when it
    # has none or the tracker has no such thing.
    parent: str | None = None


@dataclass(frozen=True)
class Source:
    """Where a goal's issues come from within its tracker: the goal's repos, which its issues' work is done
    in, and the settings the tracker asks of a goal (for Jira, its project)."""

    repos: tuple[str, ...] = ()
    settings: dict = field(default_factory=dict)


class Tracker(Protocol):
    """One issue tracker. `repo` is always the code repo, owner/name. A tracker whose issues live somewhere a
    repo does not name (a project in another system) is told where by the goal: `bound` gives the adapter
    for one goal's Source, and the calls that name a repo are made on that."""

    # The first part of every ref this tracker's issues have.
    scheme: str
    # Whether the planner tells the tracker how an issue's task is going (planner/tracker_progress.py).
    writes_progress: bool

    def bound(self, source: Source) -> "Tracker":
        """This tracker for one goal: its repos and the settings it gave. A tracker that needs neither
        answers itself."""
        ...

    async def list_open(self, repo: str, label: str | None) -> list[Issue]:
        """The repo's open issues carrying the label; every open issue when label is None. An issue whose
        repo the tracker cannot tell, among several the goal has, comes with the first repo's and `repo`
        empty: it is held until it names one."""
        ...

    async def list_assigned(self, repo: str) -> list[Issue]:
        """The repo's open issues a person handed to the meta-harness in the tracker itself, by assigning them
        to the account it signs in as, whatever their labels. Empty for a tracker with no such way, and for a
        goal that did not ask to take them."""
        ...

    async def read(self, ref: str) -> Issue:
        """One issue as it is now, open or closed."""
        ...

    async def ensure_label(self, repo: str, label: str) -> None:
        """Makes sure an issue created for the repo can carry the label."""
        ...

    async def create_issue(
        self, repo: str, title: str, body: str, labels: list[str], parent: str | None = None
    ) -> Issue:
        """Creates one issue for the repo, carrying the labels. parent is the ref of an issue to create it under,
        for a tracker that has parents (Jira); one that has none ignores it. A parent the tracker refuses is
        left off, and the issue is created without one: the returned Issue's `parent` says what was set."""
        ...

    async def comment(self, ref: str, body: str) -> str:
        """Adds a comment to the issue. Returns the comment's address, or "" when the tracker gives none."""
        ...

    async def add_section(self, ref: str, heading: str, text: str) -> None:
        """Adds a section to the end of the issue's description: the heading on a line of its own, then the
        text. The description it had is kept. Both come from `outbound.prepare`, the heading as a title and
        the text as a body for this tracker, so a plain string is refused (TypeError).

        Raises:
            TrackerError: The tracker refused the change; the message gives its reason. Not retryable, and
                nothing written, when the description already has a section with that heading or the heading
                is not one line.
        """
        ...

    async def mark(self, ref: str) -> None:
        """Makes the issue carry the meta-harness's label (MARK_LABEL, or the deployment's). An adapter calls
        it itself before every write to an issue (a comment, a status, a pull request's link) and creates
        every issue with the label, so no caller can change an issue and leave it unlabeled. Adding a label
        the issue has changes nothing."""
        ...

    async def set_status(self, ref: str, status: str) -> bool:
        """Moves the issue to one of STATUSES, as far as the tracker has such a state. Returns whether the
        issue changed: False when it was there already or the tracker has no state for it."""
        ...

    async def link_pull_request(self, ref: str, pr_url: str, title: str) -> None:
        """Records the pull request on the issue, where the tracker does not find it by itself."""
        ...

    async def assignee(self, ref: str) -> str:
        """Who the issue is assigned to, as the id `assign` takes and `choices("assignee")` lists (GitHub: a
        login; Jira: an account id; the own tracker: a console user's email). "" when no one is."""
        ...

    async def assign(self, ref: str, person: str) -> bool:
        """Makes the person the issue's assignee. Returns whether the issue changed: False, and nothing written,
        when the issue has an assignee already, the person or anyone else, since the meta-harness never replaces
        or adds to a person's assignment.

        Raises:
            TrackerError: Not retryable, and nothing written, when the tracker does not know the person or
                cannot assign them this issue.
        """
        ...

    def url(self, ref: str) -> str | None:
        """Where a person reads the issue, or None for a ref that is not this tracker's."""
        ...

    def repo_of(self, ref: str) -> str:
        """A code repo of the goal's for the issue, owner/name: the one its work is done in when the ref says,
        else one to hold it under until the issue is read.

        Raises:
            ValueError: The ref is not one of this tracker's, or not one this goal's source covers.
        """
        ...


# A heading line of a description as the planner reads it, `#` to `######`, and the words it holds.
HEADING_LINE = re.compile(r"^ {0,3}#{1,6}[ \t]+(.+?)[ \t#]*$", re.MULTILINE)


def has_section(body: str, heading: str) -> bool:
    """Whether a description, as the planner reads it, has a heading of these words, whatever their capitals."""
    wanted = " ".join(heading.split()).casefold()
    return any(" ".join(found.split()).casefold() == wanted for found in HEADING_LINE.findall(body or ""))


def check_section(ref: str, body: str, heading: str) -> None:
    """Refuses a section the description cannot take: a heading that is not one line, or one it has already.

    Raises:
        TrackerError: Not retryable, so a caller does not write the section again and again.
    """
    if not heading.strip() or "\n" in heading.strip():
        raise TrackerError(f"a section's heading is one line of words, not {heading!r}", retryable=False)
    if has_section(body, heading):
        raise TrackerError(
            f"{ref}'s description already has a section {heading.strip()!r}, so it was not added again. Edit "
            "that section in the tracker, or add the text under another heading.",
            retryable=False,
        )


def with_section(description: str, heading: str, text: str, mark: str = "##") -> str:
    """A description with a section added at its end, for `Tracker.add_section`. mark starts the heading's line
    in the tracker's markup: `##` in Markdown, `h2.` in Jira's. The earlier text is kept; only the blank space
    at its end is trimmed, so one blank line comes before the new heading.

    Raises:
        ValueError: The heading is empty.
    """
    line = " ".join(heading.split())
    if not line:
        raise ValueError("a section's heading is empty: name the section")
    section = f"{mark} {line}\n\n{text.strip()}\n"
    earlier = description.rstrip()
    return f"{earlier}\n\n{section}" if earlier else section


def scheme_of(ref: str) -> str:
    """The tracker a ref names: its part before the first colon. "" for a ref with none."""
    scheme, colon, _ = ref.partition(":")
    return scheme if colon else ""


@dataclass(frozen=True)
class Setting:
    """One setting of a tracker, as the console's form shows it."""

    name: str
    label: str
    # text; statuses: a name for each status; choice: one of what the adapter's `choices` lists for it; switch:
    # on or off, "yes" when on. A setting whose values the tracker can list is a choice, never text: the
    # console offers the list, so a name is picked and not mistyped.
    # entries: a list the operator adds to, each entry chosen from what `choices` lists for the setting and
    # holding the settings in `settings`; `entry` is what one entry is (a project), the name its choices are
    # asked by. entry_of: one of the entries of the setting named in `of`.
    kind: str = "text"
    required: bool = False
    help: str = ""
    settings: list["Setting"] = field(default_factory=list)
    entry: str = ""
    of: str = ""


# The goal setting that names who the goal's issues are assigned to, one of the people the tracker's
# `choices("assignee")` lists.
ASSIGNEE = "assignee"


def assignee_setting(people: str) -> Setting:
    """The `assignee` goal setting every tracker declares. people says whom the tracker lists."""
    return Setting(
        ASSIGNEE,
        "Assignee",
        kind="choice",
        help=f"Who the goal's issues are assigned to: one of {people}. Empty: no one is named.",
    )


def unknown_person(ref: str, person: str, where: str) -> TrackerError:
    """The error for a person the tracker cannot assign: not retryable, since asking again changes nothing."""
    return TrackerError(
        f"{ref} was not assigned: {person!r} is not one of {where}. Choose one of those for the goal's assignee, "
        "or add the person to the tracker.",
        retryable=False,
    )


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
    # What a goal that uses the tracker says besides its repos, such as the project its issues are in, for the
    # New goal form to draw; and why a goal's answers cannot be used, empty when they can.
    goal_settings: list[Setting] = field(default_factory=list)
    # site: the tracker's own settings to check against; None reads the active configuration's.
    check_goal: Callable[..., list[str]] = lambda settings, site=None: []
    # What a new goal starts with for its goal settings, from the tracker's settings: Jira's default project.
    goal_defaults: Callable[[dict[str, Any] | None], dict[str, str]] = lambda site: {}
    # Whether a goal with these settings takes the issues assigned to the meta-harness's account in the
    # tracker (`Tracker.list_assigned`): such a goal has issues to take in with no label and none picked.
    takes_assigned: Callable[[dict[str, Any]], bool] = lambda settings: False
    # Builds the adapter.
    build: Callable[[], "Tracker"] | None = None
    # Whether the planner tells the tracker how an issue's task is going (comments, status, the pull request's
    # link). False for a tracker that shows all that by itself, as GitHub does for an issue its pull request names.
    writes_progress: bool = False
