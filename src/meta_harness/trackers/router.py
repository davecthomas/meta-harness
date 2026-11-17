"""The trackers the meta-harness is connected to, as one tracker: each call goes to the adapter of the tracker
the repo's issues live in, or the one the ref names."""

from collections.abc import Callable

from meta_harness.trackers.base import Issue, Tracker, TrackerError, scheme_of


def registered_tracker(repo: str) -> str:
    """The tracker the repo registry names for the repo's issues; GitHub for a repo it does not hold."""
    from meta_harness.planner import repos

    registered = repos.get(repo)
    return registered.tracker if registered else "github"


class Trackers:
    """Routes a call by repo (where do this repo's issues live?) or by ref (which tracker's issue is this?)."""

    scheme = ""

    def __init__(self, adapters: list[Tracker], tracker_of: Callable[[str], str] = registered_tracker) -> None:
        self.adapters = {adapter.scheme: adapter for adapter in adapters}
        self.tracker_of = tracker_of

    def adapter(self, scheme: str) -> Tracker | None:
        return self.adapters.get(scheme)

    def _known(self, scheme: str, what: str) -> Tracker:
        adapter = self.adapters.get(scheme)
        if adapter is None:
            connected = ", ".join(sorted(self.adapters)) or "none"
            raise TrackerError(
                f"{what} is in the tracker {scheme or '(none named)'!r}, which is not connected (connected: "
                f"{connected}). Connect it in the planner's settings, or change the repo's tracker under "
                "Admin, Repositories.",
                retryable=False,
            )
        return adapter

    def for_repo(self, repo: str) -> Tracker:
        return self._known(self.tracker_of(repo), f"{repo}'s issues")

    def for_ref(self, ref: str) -> Tracker:
        return self._known(scheme_of(ref), ref)

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

    def url(self, ref: str) -> str | None:
        adapter = self.adapters.get(scheme_of(ref))
        return adapter.url(ref) if adapter else None
