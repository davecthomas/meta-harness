"""The trackers the meta-harness is connected to, as one tracker: each call goes to the adapter of the tracker
the repo's issues live in, or the one the ref names."""

from collections.abc import Callable

from meta_harness.trackers import registry
from meta_harness.trackers.base import Issue, Source, Tracker, TrackerError, scheme_of
from meta_harness.trackers.own import SCHEME as OWN


def _under(parent: str | None) -> dict[str, str]:
    """The parent to pass on to an adapter's create_issue: none at all when there is none, so an adapter that
    takes no parent is called as before."""
    return {"parent": parent} if parent else {}


class WithOwn:
    """A goal's tracker together with the meta-harness's own (trackers/own.py), as one tracker.

    A goal's issues are its tracker's, and may also include tasks its plan added that are kept in the
    meta-harness. Every call that names an issue goes to the tracker its ref names, so the planner reads,
    closes and tells either kind the same way. A call that names no issue goes to the goal's tracker, and
    listing by label adds the own issues carrying it.
    """

    # The own tracker's issues are told how their tasks go, whatever the goal's tracker asks for itself.
    writes_progress = True

    def __init__(self, primary: Tracker, own: Tracker) -> None:
        self.primary, self.own = primary, own
        self.scheme = primary.scheme

    def _of(self, ref: str) -> Tracker:
        return self.own if scheme_of(ref) == self.own.scheme else self.primary

    def bound(self, source: Source) -> "WithOwn":
        return WithOwn(self.primary.bound(source), self.own.bound(source))

    async def list_open(self, repo: str, label: str | None) -> list[Issue]:
        found = await self.primary.list_open(repo, label)
        # Only by label: with none, the question is what the goal's tracker has to pick from.
        return found if label is None else found + await self.own.list_open(repo, label)

    async def list_assigned(self, repo: str) -> list[Issue]:
        return await self.primary.list_assigned(repo)

    async def read(self, ref: str) -> Issue:
        return await self._of(ref).read(ref)

    async def ensure_label(self, repo: str, label: str) -> None:
        await self.primary.ensure_label(repo, label)

    async def create_issue(
        self, repo: str, title: str, body: str, labels: list[str], parent: str | None = None
    ) -> Issue:
        return await self.primary.create_issue(repo, title, body, labels, **_under(parent))

    async def create_epic(self, repo: str, title: str, body: str) -> Issue:
        return await self.primary.create_epic(repo, title, body)

    def _told(self, ref: str) -> Tracker | None:
        """The tracker to tell of the issue's progress; None for one that shows it by itself."""
        tracker = self._of(ref)
        return tracker if getattr(tracker, "writes_progress", False) else None

    async def comment(self, ref: str, body: str) -> str:
        tracker = self._told(ref)
        return await tracker.comment(ref, body) if tracker else ""

    async def mark(self, ref: str) -> None:
        await self._of(ref).mark(ref)

    async def set_status(self, ref: str, status: str) -> bool:
        tracker = self._told(ref)
        return await tracker.set_status(ref, status) if tracker else False

    async def close_unplanned(self, ref: str) -> bool:
        # The operator's own decision on the issue, not its progress: made whatever the tracker shows by itself.
        return await self._of(ref).close_unplanned(ref)

    async def link_pull_request(self, ref: str, pr_url: str, title: str) -> None:
        tracker = self._told(ref)
        if tracker:
            await tracker.link_pull_request(ref, pr_url, title)

    async def add_section(self, ref: str, heading: str, text: str) -> None:
        # The issue's own content, not its progress: written whatever the tracker shows by itself.
        await self._of(ref).add_section(ref, heading, text)

    async def assignee(self, ref: str) -> str:
        return await self._of(ref).assignee(ref)

    async def assign(self, ref: str, person: str) -> bool:
        # Who the issue is assigned to, not its progress: set whatever the tracker shows by itself.
        return await self._of(ref).assign(ref, person)

    def repo_of(self, ref: str) -> str:
        return self._of(ref).repo_of(ref)

    def url(self, ref: str) -> str | None:
        return self._of(ref).url(ref)

    def __getattr__(self, name: str):
        # What only the goal's tracker has, such as an adapter's `choices`.
        return getattr(self.primary, name)


class Trackers:
    """Routes a call by ref (which tracker's issue is this?), by goal (a goal uses one tracker), or, with
    neither, to the default tracker."""

    scheme = ""

    def __init__(self, adapters: list[Tracker], default: Callable[[], str] = registry.default) -> None:
        self.adapters = {adapter.scheme: adapter for adapter in adapters}
        self.default = default

    def adapter(self, scheme: str) -> Tracker | None:
        return self.adapters.get(scheme)

    def named(self, scheme: str, what: str = "") -> Tracker:
        """The adapter of a tracker by name.

        Raises:
            TrackerError: The meta-harness has no such tracker.
        """
        adapter = self.adapters.get(scheme)
        if adapter is None:
            supported = ", ".join(sorted(self.adapters)) or "none"
            raise TrackerError(
                f"{what or 'this'} names the tracker {scheme or '(none)'!r}, which Octofusion does not "
                f"support (supported: {supported}). Choose one of those under Configuration, Issue trackers, "
                "or for the goal.",
                retryable=False,
            )
        return adapter

    def for_goal(self, goal: dict) -> Tracker:
        """The goal's tracker: the one it was created with, GitHub for a goal from before goals named one."""
        adapter = self.named(goal.get("tracker") or "github", f"goal {goal.get('id', '')}".strip())
        source = Source(tuple(goal.get("repos") or ()), dict(goal.get("tracker_settings") or {}))
        own = self.adapters.get(OWN)
        if own is None or adapter.scheme == OWN:
            return adapter.bound(source)
        # A tracked goal may still hold tasks its plan kept in the meta-harness.
        return WithOwn(adapter.bound(source), own.bound(source))

    def for_repo(self, repo: str) -> Tracker:
        """With no goal to say, a repo's issues are read from the default tracker."""
        return self.named(self.default(), "the default")

    def for_ref(self, ref: str) -> Tracker:
        return self.named(scheme_of(ref), ref)

    async def list_open(self, repo: str, label: str | None) -> list[Issue]:
        return await self.for_repo(repo).list_open(repo, label)

    async def list_assigned(self, repo: str) -> list[Issue]:
        return await self.for_repo(repo).list_assigned(repo)

    async def read(self, ref: str) -> Issue:
        return await self.for_ref(ref).read(ref)

    async def ensure_label(self, repo: str, label: str) -> None:
        await self.for_repo(repo).ensure_label(repo, label)

    async def create_issue(
        self, repo: str, title: str, body: str, labels: list[str], parent: str | None = None
    ) -> Issue:
        return await self.for_repo(repo).create_issue(repo, title, body, labels, **_under(parent))

    async def create_epic(self, repo: str, title: str, body: str) -> Issue:
        return await self.for_repo(repo).create_epic(repo, title, body)

    async def comment(self, ref: str, body: str) -> str:
        return await self.for_ref(ref).comment(ref, body)

    async def mark(self, ref: str) -> None:
        await self.for_ref(ref).mark(ref)

    async def set_status(self, ref: str, status: str) -> bool:
        return await self.for_ref(ref).set_status(ref, status)

    async def close_unplanned(self, ref: str) -> bool:
        return await self.for_ref(ref).close_unplanned(ref)

    async def link_pull_request(self, ref: str, pr_url: str, title: str) -> None:
        await self.for_ref(ref).link_pull_request(ref, pr_url, title)

    async def add_section(self, ref: str, heading: str, text: str) -> None:
        await self.for_ref(ref).add_section(ref, heading, text)

    async def assignee(self, ref: str) -> str:
        return await self.for_ref(ref).assignee(ref)

    async def assign(self, ref: str, person: str) -> bool:
        return await self.for_ref(ref).assign(ref, person)

    def repo_of(self, ref: str) -> str:
        return self.for_ref(ref).repo_of(ref)

    def url(self, ref: str) -> str | None:
        adapter = self.adapters.get(scheme_of(ref))
        return adapter.url(ref) if adapter else None
