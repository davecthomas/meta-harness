"""GitHub Issues as a tracker. A repo's issues live in the repo itself.

An issue's ref is `github:<owner>/<repo>#<number>`. Every call goes through the repo owner's GitHub App, with
`issues: read` to read and `issues: write` to create, comment or close.
"""

import re

from meta_harness import outbound
from meta_harness.github_app import GitHubAppError, GitHubApps
from meta_harness.run_service.code_host import acceptance_checks, issue_paths
from collections.abc import Callable

from meta_harness.trackers.base import (
    DONE,
    TODO,
    Issue,
    Marked,
    Source,
    Supported,
    TrackerError,
    configured_label,
    refuse_twice,
    section_heading,
    with_section,
)

SCHEME = "github"
READ_ISSUES = {"issues": "read"}
# Creating a plan's issues and the goal's label for them, commenting, and closing.
WRITE_ISSUES = {"issues": "write"}
REF = re.compile(r"^github:(?P<repo>[\w.-]+/[\w.-]+)#(?P<number>\d+)$")
# "Depends on: #12, owner/repo#13" lines in an issue body.
DEPENDS_LINE = re.compile(r"^\s*depends on:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
DEPENDS_ITEM = re.compile(r"(?:(?P<repo>[\w.-]+/[\w.-]+))?#(?P<number>\d+)")


def ref_for(repo: str, number: int) -> str:
    return f"github:{repo}#{number}"


def parse_ref(ref: str) -> tuple[str, int]:
    match = REF.match(ref)
    if not match:
        raise ValueError(f"{ref!r} is not github:<owner>/<repo>#<number>")
    return match["repo"], int(match["number"])


def depends_on(repo: str, body: str) -> list[str]:
    """The issues a body's `Depends on:` lines name, as refs. A bare #12 is in the same repo."""
    refs: list[str] = []
    for line in DEPENDS_LINE.findall(body or ""):
        for item in DEPENDS_ITEM.finditer(line):
            ref = ref_for(item["repo"] or repo, int(item["number"]))
            if ref not in refs:
                refs.append(ref)
    return refs


def _issue(repo: str, data: dict) -> Issue:
    body = data.get("body") or ""
    return Issue(
        ref=ref_for(repo, data["number"]),
        repo=repo,
        number=data["number"],
        title=data["title"],
        body=body,
        labels=[label["name"] for label in data.get("labels", [])],
        state=data["state"],
        depends_on=depends_on(repo, body),
        checks=acceptance_checks(body),
        updated_at=data.get("updated_at") or "",
        paths=issue_paths(body),
        author=(data.get("user") or {}).get("login") or "",
        author_association=data.get("author_association") or "",
    )


class GitHubTracker:
    scheme = SCHEME
    # A pull request's `Closes` line links it to its issue and closes the issue on merge: nothing to tell.
    writes_progress = False

    def __init__(self, apps: GitHubApps | None = None, label: Callable[[], str] = configured_label) -> None:
        self.apps = apps or GitHubApps()
        self.label = label
        self.marked = Marked()

    async def mark(self, ref: str) -> None:
        """Adds the meta-harness's label to the issue. GitHub makes a label the repo lacks when an issue first
        carries it, and adding one the issue has changes nothing."""
        label = self.label()
        if (ref, label) in self.marked.refs:
            return
        repo, number = parse_ref(ref)
        await self._call(repo, WRITE_ISSUES, "POST", f"/repos/{repo}/issues/{number}/labels", json={"labels": [label]})
        self.marked.refs.add((ref, label))

    async def _call(self, repo: str, permissions: dict[str, str], method: str, path: str, **kwargs):
        """One GitHub call, its failure as a TrackerError, so no caller needs to know the tracker is GitHub."""
        try:
            return await self.apps.call(repo, permissions, method, path, **kwargs)
        except GitHubAppError as err:
            raise TrackerError(str(err), retryable=err.retryable, status=err.status) from err

    async def list_open(self, repo: str, label: str | None) -> list[Issue]:
        issues: list[Issue] = []
        page = 1
        params: dict = {"state": "open", "per_page": 100}
        if label is not None:
            params["labels"] = label
        while True:
            batch = await self._call(repo, READ_ISSUES, "GET", f"/repos/{repo}/issues", params={**params, "page": page})
            # The issues endpoint lists pull requests too.
            issues += [_issue(repo, item) for item in batch if "pull_request" not in item]
            if len(batch) < 100:
                return issues
            page += 1

    async def list_assigned(self, repo: str) -> list[Issue]:
        """None: the meta-harness acts on GitHub as a GitHub App, and GitHub assigns issues only to people. On
        GitHub the goal's label is how a person hands an issue over."""
        return []

    async def read(self, ref: str) -> Issue:
        repo, number = parse_ref(ref)
        return _issue(repo, await self._call(repo, READ_ISSUES, "GET", f"/repos/{repo}/issues/{number}"))

    async def ensure_label(self, repo: str, label: str) -> None:
        """Makes sure the repo has the label, so an issue created with it carries it."""
        try:
            await self._call(
                repo,
                WRITE_ISSUES,
                "POST",
                f"/repos/{repo}/labels",
                json={"name": label, "color": "5319e7", "description": "A meta-harness goal's issues"},
            )
        except TrackerError as err:
            # 422: the label is there already.
            if err.status != 422:
                raise

    async def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> Issue:
        """Creates one issue. Only the planner calls it, and only for a plan an operator approved."""
        outbound.require(title, "an issue's title", outbound.GITHUB, title=True)
        outbound.require(body, "an issue's body", outbound.GITHUB)
        carried = list(dict.fromkeys([*labels, self.label()]))
        created = await self._call(
            repo, WRITE_ISSUES, "POST", f"/repos/{repo}/issues", json={"title": title, "body": body, "labels": carried}
        )
        made = _issue(repo, created)
        self.marked.refs.add((made.ref, self.label()))
        return made

    async def comment(self, ref: str, body: str) -> str:
        outbound.require(body, "a comment on an issue", outbound.GITHUB)
        repo, number = parse_ref(ref)
        await self.mark(ref)
        made = await self._call(
            repo, WRITE_ISSUES, "POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body}
        )
        return str(made.get("html_url") or "")

    async def set_status(self, ref: str, status: str) -> bool:
        """A GitHub issue is open or closed, nothing between: done closes it, todo opens it again, and a status
        in progress or in review changes nothing. A pull request's `Closes` line closes its issue by itself
        when it merges; this is for an issue no merged pull request names."""
        wanted = {DONE: "closed", TODO: "open"}.get(status)
        if wanted is None:
            return False
        repo, number = parse_ref(ref)
        now = await self._call(repo, READ_ISSUES, "GET", f"/repos/{repo}/issues/{number}")
        if now["state"] == wanted:
            return False
        await self.mark(ref)
        change = {"state": wanted, **({"state_reason": "completed"} if wanted == "closed" else {})}
        await self._call(repo, WRITE_ISSUES, "PATCH", f"/repos/{repo}/issues/{number}", json=change)
        return True

    async def add_section(self, ref: str, heading: str, text: str) -> None:
        """Appends `## <heading>` and the text to the issue's body, read just before."""
        outbound.require(text, "a section of an issue's description", outbound.GITHUB)
        heading = section_heading(heading)
        repo, number = parse_ref(ref)
        now = await self._call(repo, READ_ISSUES, "GET", f"/repos/{repo}/issues/{number}")
        body = now.get("body") or ""
        refuse_twice(ref, body, heading)
        await self.mark(ref)
        await self._call(
            repo,
            WRITE_ISSUES,
            "PATCH",
            f"/repos/{repo}/issues/{number}",
            json={"body": with_section(body, f"## {heading}", text)},
        )

    async def link_pull_request(self, ref: str, pr_url: str, title: str) -> None:
        """Nothing to do: GitHub links a pull request to the issue its description's `Closes` line names."""

    def bound(self, source: Source) -> "GitHubTracker":
        """A repo's issues are the repo's own: a goal's repos are all GitHub needs, and each call names one."""
        return self

    def repo_of(self, ref: str) -> str:
        return parse_ref(ref)[0]

    def url(self, ref: str) -> str | None:
        match = REF.match(ref)
        return f"https://github.com/{match['repo']}/issues/{match['number']}" if match else None


SPEC = Supported(
    SCHEME,
    "GitHub Issues",
    "Each repo's issues are its own GitHub issues, read and written through the repo owner's GitHub App.",
    build=GitHubTracker,
)
