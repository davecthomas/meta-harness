"""GitHub Issues as a tracker. A repo's issues live in the repo itself.

An issue's ref is `github:<owner>/<repo>#<number>`. Every call goes through the repo owner's GitHub App, with
`issues: read` to read and `issues: write` to create, comment or close.
"""

import re
from typing import Any

from meta_harness import outbound
from meta_harness.github_app import GitHubAppError, GitHubApps
from meta_harness.run_service.code_host import acceptance_checks, issue_paths
from collections.abc import Callable

from meta_harness.trackers.base import (
    ASSIGNEE,
    DONE,
    TODO,
    Issue,
    Marked,
    Source,
    Supported,
    TrackerError,
    assignee_setting,
    check_assignee,
    check_section,
    configured_label,
    unknown_person,
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
REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
# A GitHub login: letters, digits and single hyphens, up to 39 characters.
LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


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


def _assignees(data: dict) -> list[str]:
    """The logins an issue is assigned to, as GitHub gives the issue."""
    found = [person["login"] for person in data.get("assignees") or [] if person.get("login")]
    one = (data.get("assignee") or {}).get("login")
    return found or ([one] if one else [])


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


def check_goal(settings: dict[str, Any], site: dict[str, Any] | None = None) -> list[str]:
    """Why a goal's GitHub settings cannot be used; empty when they can. Its one setting, `assignee`, is a
    login when it is set."""
    return check_assignee(settings, lambda value: bool(LOGIN.match(value)), "a GitHub login, such as octocat")


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

    async def create_issue(
        self, repo: str, title: str, body: str, labels: list[str], parent: str | None = None
    ) -> Issue:
        """Creates one issue. Only the planner calls it, and only for a plan an operator approved. A GitHub
        issue has no parent here, so parent is ignored."""
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

    async def add_section(self, ref: str, heading: str, text: str) -> None:
        """Adds a `## heading` section to the end of the issue's body, keeping what the body says. A heading
        the body has already is refused before anything is written."""
        outbound.require(heading, "a section's heading", outbound.GITHUB, title=True)
        outbound.require(text, "a section of an issue's body", outbound.GITHUB)
        repo, number = parse_ref(ref)
        now = await self._call(repo, READ_ISSUES, "GET", f"/repos/{repo}/issues/{number}")
        check_section(ref, now.get("body") or "", heading)
        body = with_section(now.get("body") or "", heading, text)
        await self.mark(ref)
        await self._call(repo, WRITE_ISSUES, "PATCH", f"/repos/{repo}/issues/{number}", json={"body": body})

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

    async def link_pull_request(self, ref: str, pr_url: str, title: str) -> None:
        """Nothing to do: GitHub links a pull request to the issue its description's `Closes` line names."""

    async def assignee(self, ref: str) -> str:
        """The login of the issue's first assignee; "" when it has none."""
        repo, number = parse_ref(ref)
        found = _assignees(await self._call(repo, READ_ISSUES, "GET", f"/repos/{repo}/issues/{number}"))
        return found[0] if found else ""

    async def assign(self, ref: str, person: str) -> bool:
        """Makes the login the assignee of an issue that has none. GitHub drops a login it cannot assign without
        saying so, so the login is checked against the repo's assignable users first, and refused before
        anything is written when it is not one of them."""
        repo, number = parse_ref(ref)
        login = person.strip()
        if not LOGIN.match(login) or not await self._assignable(repo, login):
            raise unknown_person(ref, person, f"the people who can be assigned issues in {repo}")
        now = await self._call(repo, READ_ISSUES, "GET", f"/repos/{repo}/issues/{number}")
        if _assignees(now):
            return False
        await self.mark(ref)
        await self._call(repo, WRITE_ISSUES, "PATCH", f"/repos/{repo}/issues/{number}", json={"assignees": [login]})
        return True

    async def _assignable(self, repo: str, login: str) -> bool:
        """Whether the repo's issues can be assigned to the login: GitHub answers 204 when they can, 404 not."""
        try:
            await self._call(repo, READ_ISSUES, "GET", f"/repos/{repo}/assignees/{login}")
        except TrackerError as err:
            if err.status == 404:
                return False
            raise
        return True

    async def _assignable_users(self, repo: str) -> dict[str, str]:
        """Every login the repo's issues can be assigned to, by its lower case."""
        found: dict[str, str] = {}
        page = 1
        while True:
            params = {"per_page": 100, "page": page}
            batch = await self._call(repo, READ_ISSUES, "GET", f"/repos/{repo}/assignees", params=params) or []
            found.update({user["login"].lower(): user["login"] for user in batch if user.get("login")})
            if len(batch) < 100:
                return found
            page += 1

    async def people(self, repo: str) -> list[dict[str, str]]:
        """The logins the repo's issues can be assigned to, each named by itself."""
        if not REPO.match(repo):
            raise TrackerError(f"{repo!r} is not a repo's owner/name, such as octo/api", retryable=False)
        logins = await self._assignable_users(repo)
        return [{"value": login, "label": login} for login in sorted(logins.values(), key=str.lower)]

    async def choices(self, setting: str, scope: dict[str, str] | None = None) -> list[dict[str, str]]:
        """What the console offers for a setting, so a name is picked and not typed.

        - `assignee`, with scope's `repos` (owner/name, comma-separated) or `repo`: the people who can be
          assigned issues in every one of those repos, by login, so the one picked can be assigned any of the
          goal's issues. Empty with no repo.
        """
        if setting != ASSIGNEE:
            return []
        given = f"{(scope or {}).get('repos', '')},{(scope or {}).get('repo', '')}"
        repos = list(dict.fromkeys(name.strip() for name in given.split(",") if name.strip()))
        common: list[dict[str, str]] | None = None
        for repo in repos:
            found = await self.people(repo)
            if common is None:
                common = found
            else:
                logins = {person["value"].lower() for person in found}
                common = [person for person in common if person["value"].lower() in logins]
        return common or []

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
    goal_settings=[assignee_setting("the people who can be assigned issues in all the goal's repos, by login")],
    check_goal=check_goal,
)
