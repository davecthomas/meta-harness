"""The trackers the meta-harness is connected to, as one tracker: each call goes to the adapter of the tracker
the repo's issues live in, or the one the ref names."""

from collections.abc import Callable

from meta_harness.trackers import registry
from meta_harness.trackers.base import Issue, Tracker, TrackerError, scheme_of


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
                f"{what or 'this'} names the tracker {scheme or '(none)'!r}, which the meta-harness does not "
                f"support (supported: {supported}). Choose one of those under Configuration, Issue trackers, "
                "or for the goal.",
                retryable=False,
            )
        return adapter

    def for_goal(self, goal: dict) -> Tracker:
        """The goal's tracker: the one it was created with, GitHub for a goal from before goals named one."""
        return self.named(goal.get("tracker") or "github", f"goal {goal.get('id', '')}".strip())

    def for_repo(self, repo: str) -> Tracker:
        """With no goal to say, a repo's issues are read from the default tracker."""
        return self.named(self.default(), "the default")

    def for_ref(self, ref: str) -> Tracker:
        return self.named(scheme_of(ref), ref)

    async def list_open(self, repo: str, label: str | None) -> list[Issue]:
        return await self.for_repo(repo).list_open(repo, label)

    async def read(self, ref: str) -> Issue:
        return await self.for_ref(ref).read(ref)

    async def ensure_label(self, repo: str, label: str) -> None:
        await self.for_repo(repo).ensure_label(repo, label)

    async def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> Issue:
        return await self.for_repo(repo).create_issue(repo, title, body, labels)

    async def comment(self, ref: str, body: str) -> str:
        return await self.for_ref(ref).comment(ref, body)

    async def set_status(self, ref: str, status: str) -> bool:
        return await self.for_ref(ref).set_status(ref, status)

    async def link_pull_request(self, ref: str, pr_url: str, title: str) -> None:
        await self.for_ref(ref).link_pull_request(ref, pr_url, title)

    def repo_of(self, ref: str) -> str:
        return self.for_ref(ref).repo_of(ref)

    def url(self, ref: str) -> str | None:
        adapter = self.adapters.get(scheme_of(ref))
        return adapter.url(ref) if adapter else None
