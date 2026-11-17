"""Jira Cloud as a tracker. Issues live in Jira projects, and each project's issues are worked on in one repo.

An issue's ref is `jira:<KEY>`, such as `jira:PROJ-123`. The `trackers` configuration says where the issues
are: `"jira": {"site": "https://example.atlassian.net", "projects": [{"key": "PROJ", "repo": "owner/name"}]}`,
with optional `issue_type` (the type of an issue the planner creates; Task when unset) and `statuses` (the
projects' own name for todo, in_progress, in_review or done, where their workflow's names are not the usual).
One project goes to one repo, so a ref's project names its repo; a repo may have several projects.

Calls sign in as one Jira account: MH_JIRA_EMAIL and MH_JIRA_TOKEN, an API token of that account. They use
REST API version 2, whose descriptions and comments are text (wiki markup), which is what the planner reads an
issue's `## Acceptance checks`, `Paths:` and `Depends on:` lines from.
"""

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from meta_harness.run_service.code_host import acceptance_checks, issue_paths
from meta_harness.trackers.base import (
    DONE,
    IN_PROGRESS,
    IN_REVIEW,
    STATUSES,
    TODO,
    Issue,
    Setting,
    Supported,
    TrackerError,
)

SCHEME = "jira"
KEY = re.compile(r"^(?P<project>[A-Z][A-Z0-9_]+)-(?P<number>\d+)$")
REF = re.compile(r"^jira:(?P<key>[A-Z][A-Z0-9_]+-\d+)$")
PROJECT = re.compile(r"^[A-Z][A-Z0-9_]+$")
FIELDS = ["summary", "description", "labels", "status", "updated", "reporter", "issuelinks"]
PAGE = 100
TIMEOUT_SECONDS = 30.0
# "Depends on: PROJ-12, PROJ-13" lines in a description, beside the issue's own "is blocked by" links.
DEPENDS_LINE = re.compile(r"^\s*depends on:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
DEPENDS_KEY = re.compile(r"\b[A-Z][A-Z0-9_]+-\d+\b")
# Jira's three status categories, which every workflow's statuses belong to, for each status of ours.
CATEGORY = {TODO: "new", IN_PROGRESS: "indeterminate", IN_REVIEW: "indeterminate", DONE: "done"}


@dataclass(frozen=True)
class Place:
    """Where a repo's issues are in Jira."""

    repo: str
    site: str
    project: str
    issue_type: str = "Task"
    statuses: dict[str, str] = field(default_factory=dict)


def ref_for(key: str) -> str:
    return f"jira:{key}"


def parse_ref(ref: str) -> str:
    """The issue key a ref names."""
    match = REF.match(ref)
    if not match:
        raise ValueError(f"{ref!r} is not jira:<PROJECT>-<number>")
    return match["key"]


def places() -> list[Place]:
    """Every Jira project the trackers configuration lists, with the repo its issues are worked on in."""
    from meta_harness.trackers import registry

    jira = registry.settings(SCHEME) or {}
    return [
        Place(
            repo=str(entry.get("repo", "")),
            site=str(jira.get("site", "")).rstrip("/"),
            project=str(entry.get("key", "")),
            issue_type=str(jira.get("issue_type") or "Task"),
            statuses=dict(jira.get("statuses") or {}),
        )
        for entry in jira.get("projects") or []
        if isinstance(entry, dict)
    ]


def markdown(text: str) -> str:
    """A Jira description as the planner reads issue bodies: headings as `#` lines, code in backticks."""
    text = re.sub(r"^h([1-6])\.\s+", lambda m: "#" * int(m[1]) + " ", text or "", flags=re.MULTILINE)
    text = re.sub(r"\{\{(.+?)\}\}", r"`\1`", text)
    return re.sub(r"\{(?:code|noformat)(?::[^}]*)?\}", "```", text)


def depends_on(issue: dict[str, Any], body: str) -> list[str]:
    """The issues that block this one: its "is blocked by" links, and the keys its `Depends on:` lines name."""
    keys = [
        link["inwardIssue"]["key"]
        for link in issue["fields"].get("issuelinks") or []
        if "inwardIssue" in link and (link.get("type") or {}).get("inward", "").lower() == "is blocked by"
    ]
    for line in DEPENDS_LINE.findall(body):
        keys += DEPENDS_KEY.findall(line)
    return [ref_for(key) for key in dict.fromkeys(keys) if key != issue["key"]]


class JiraTracker:
    scheme = SCHEME
    # Nothing in Jira shows a task's progress, or closes its issue, unless the planner says.
    writes_progress = True

    def __init__(
        self,
        email: str | None = None,
        token: str | None = None,
        places: Callable[[], list[Place]] = places,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.email = email if email is not None else os.environ.get("MH_JIRA_EMAIL", "")
        self.token = token if token is not None else os.environ.get("MH_JIRA_TOKEN", "")
        self.places = places
        self.transport = transport

    @property
    def connected(self) -> bool:
        """Whether the Jira account's sign-in is set."""
        return bool(self.email and self.token)

    def _usable(self) -> list[Place]:
        return [p for p in self.places() if p.site.startswith("https://") and PROJECT.match(p.project) and p.repo]

    def _for_repo(self, repo: str) -> list[Place]:
        """The Jira projects whose issues are worked on in the repo."""
        found = [place for place in self._usable() if place.repo.lower() == repo.lower()]
        if not found:
            raise TrackerError(
                f"no Jira project's issues go to {repo}: list the project with this repo under Configuration, "
                "Issue trackers, Jira",
                retryable=False,
            )
        return found

    def _for_key(self, key: str) -> Place:
        project = key.rsplit("-", 1)[0]
        for place in self._usable():
            if place.project == project:
                return place
        raise TrackerError(
            f"the Jira project {project} is not set up: list it with its repo under Configuration, Issue "
            "trackers, Jira",
            retryable=False,
        )

    def repo_of(self, ref: str) -> str:
        """The code repo an issue's work is done in: the one whose registry entry names the issue's project."""
        return self._for_key(parse_ref(ref)).repo

    async def _call(self, place: Place, method: str, path: str, **kwargs: Any) -> Any:
        if not self.email or not self.token:
            raise TrackerError(
                "Jira is not signed in: add the secrets MH_JIRA_EMAIL (the Jira account) and MH_JIRA_TOKEN (an "
                "API token of that account) to the deployment, then deploy",
                retryable=False,
            )
        try:
            async with httpx.AsyncClient(
                base_url=place.site, auth=(self.email, self.token), timeout=TIMEOUT_SECONDS, transport=self.transport
            ) as http:
                response = await http.request(method, f"/rest/api/2{path}", **kwargs)
        except httpx.HTTPError as err:
            raise TrackerError(f"Jira did not answer {method} {path}: {type(err).__name__}: {err}") from err
        if response.status_code >= 400:
            why = _why(response)
            hint = {
                401: " Check MH_JIRA_EMAIL and MH_JIRA_TOKEN: the token may have expired or been revoked.",
                403: f" The Jira account needs that permission in the project {place.project}.",
                404: " The issue or project may not exist, or the Jira account may not see it.",
            }.get(response.status_code, "")
            retryable = response.status_code >= 500 or response.status_code == 429
            raise TrackerError(
                f"Jira refused {method} {path} ({response.status_code}): {why}.{hint}",
                retryable=retryable,
                status=response.status_code,
            )
        return response.json() if response.content else {}

    def _issue(self, place: Place, data: dict[str, Any]) -> Issue:
        fields = data["fields"]
        body = markdown(fields.get("description") or "")
        done = ((fields.get("status") or {}).get("statusCategory") or {}).get("key") == "done"
        return Issue(
            ref=ref_for(data["key"]),
            repo=place.repo,
            number=int(data["key"].rsplit("-", 1)[1]),
            title=fields.get("summary") or "",
            body=body,
            labels=list(fields.get("labels") or []),
            state="closed" if done else "open",
            depends_on=depends_on(data, body),
            checks=acceptance_checks(body),
            updated_at=fields.get("updated") or "",
            paths=issue_paths(body),
            author=(fields.get("reporter") or {}).get("displayName") or "",
        )

    async def list_open(self, repo: str, label: str | None) -> list[Issue]:
        issues: list[Issue] = []
        for place in self._for_repo(repo):
            issues += await self._list_open(place, label)
        return issues

    async def _list_open(self, place: Place, label: str | None) -> list[Issue]:
        jql = f'project = "{place.project}" AND statusCategory != Done'
        if label is not None:
            jql += f" AND labels = {_quoted(label)}"
        issues: list[Issue] = []
        token: str | None = None
        while True:
            ask = {"jql": f"{jql} ORDER BY created ASC", "fields": FIELDS, "maxResults": PAGE}
            page = await self._call(
                place, "POST", "/search/jql", json={**ask, **({"nextPageToken": token} if token else {})}
            )
            issues += [self._issue(place, item) for item in page.get("issues") or []]
            token = page.get("nextPageToken")
            if not token or page.get("isLast"):
                return issues

    async def read(self, ref: str) -> Issue:
        key = parse_ref(ref)
        place = self._for_key(key)
        return self._issue(place, await self._call(place, "GET", f"/issue/{key}", params={"fields": ",".join(FIELDS)}))

    async def ensure_label(self, repo: str, label: str) -> None:
        """Jira makes a label when an issue first carries it, so there is nothing to create; a label Jira could
        not hold is refused here, before any issue is made."""
        self._for_repo(repo)
        if not label or re.search(r"\s", label):
            raise TrackerError(
                f"a Jira label has no spaces, and the goal's label is {label!r}: change the goal's label to one "
                "without spaces",
                retryable=False,
            )

    async def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> Issue:
        # A repo with several projects gets its new issues in the first one listed.
        place = self._for_repo(repo)[0]
        made = await self._call(
            place,
            "POST",
            "/issue",
            json={
                "fields": {
                    "project": {"key": place.project},
                    "summary": title,
                    "description": body,
                    "issuetype": {"name": place.issue_type},
                    "labels": labels,
                }
            },
        )
        return await self.read(ref_for(made["key"]))

    async def comment(self, ref: str, body: str) -> str:
        key = parse_ref(ref)
        place = self._for_key(key)
        made = await self._call(place, "POST", f"/issue/{key}/comment", json={"body": body})
        return f"{place.site}/browse/{key}?focusedCommentId={made['id']}" if made.get("id") else ""

    async def set_status(self, ref: str, status: str) -> bool:
        """Moves the issue through its workflow to the status the project names for ours, or, with no name
        given, to a status of the matching Jira category (To Do, In Progress, Done). For in_review a status
        with "review" in its name is taken when the workflow has one; else an issue already in progress stays.

        Raises:
            TrackerError: The issue's workflow has no move to such a status from where it is.
        """
        if status not in STATUSES:
            raise ValueError(f"{status!r} is not one of {', '.join(STATUSES)}")
        key = parse_ref(ref)
        place = self._for_key(key)
        now = (await self._call(place, "GET", f"/issue/{key}", params={"fields": "status"}))["fields"]["status"]
        named = place.statuses.get(status, "").strip().lower()
        category = (now.get("statusCategory") or {}).get("key")
        here = now.get("name", "").lower()
        if named and here == named:
            return False
        moves = (await self._call(place, "GET", f"/issue/{key}/transitions")).get("transitions") or []

        def to(move: dict) -> tuple[str, str]:
            target = move.get("to") or {}
            return target.get("name", "").lower(), (target.get("statusCategory") or {}).get("key", "")

        if named:
            chosen = [m for m in moves if to(m)[0] == named]
        else:
            if status == IN_REVIEW:
                if "review" in here:
                    return False
                chosen = [m for m in moves if "review" in to(m)[0]]
                if not chosen and category == "indeterminate":
                    return False
            else:
                chosen = []
                if category == CATEGORY[status] and not (status == IN_PROGRESS and "review" in here):
                    return False
            chosen = chosen or [m for m in moves if to(m)[1] == CATEGORY[status] and "review" not in to(m)[0]]
            chosen = chosen or [m for m in moves if to(m)[1] == CATEGORY[status]]
        if not chosen:
            can = ", ".join(sorted(m.get("to", {}).get("name", "?") for m in moves)) or "nowhere"
            raise TrackerError(
                f"{key} is {now.get('name')!r} and its workflow has no move to "
                f"{named or status.replace('_', ' ')!r} from there (it can go to: {can}). Name the project's "
                f"status for {status} under Configuration, Issue trackers, Jira, or move the issue in Jira.",
                retryable=False,
            )
        await self._call(place, "POST", f"/issue/{key}/transitions", json={"transition": {"id": chosen[0]["id"]}})
        return True

    async def link_pull_request(self, ref: str, pr_url: str, title: str) -> None:
        """Adds the pull request to the issue's links. Its address is the link's id, so adding it again
        changes the one link."""
        key = parse_ref(ref)
        place = self._for_key(key)
        link = {"globalId": pr_url, "object": {"url": pr_url, "title": title or pr_url}}
        await self._call(place, "POST", f"/issue/{key}/remotelink", json=link)

    def issue_url_prefix(self) -> str | None:
        """The start of every issue's address, which its key follows; None until the site is set."""
        site = next((place.site for place in self._usable()), "")
        return f"{site}/browse/" if site else None

    def url(self, ref: str) -> str | None:
        match = REF.match(ref)
        if not match:
            return None
        try:
            return f"{self._for_key(match['key']).site}/browse/{match['key']}"
        except TrackerError:
            return None


def _quoted(text: str) -> str:
    """A string as JQL takes one."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _why(response: httpx.Response) -> str:
    """What Jira said was wrong, from its error body."""
    try:
        body = response.json()
    except ValueError:
        return (response.text or "no reason given")[:300]
    said = list(body.get("errorMessages") or []) + [f"{k}: {v}" for k, v in (body.get("errors") or {}).items()]
    return ("; ".join(said) or body.get("message") or "no reason given")[:300]


def check_settings(jira: dict[str, Any], repos: set[str] | None) -> list[str]:
    """Why Jira's settings cannot be used; empty when they can."""
    reasons = []
    site = jira.get("site")
    if not isinstance(site, str) or not re.fullmatch(r"https://[a-z0-9.-]+\.[a-z]{2,}/?", site):
        reasons.append("site is the Jira site's address, such as https://example.atlassian.net")
    projects = jira.get("projects")
    if not isinstance(projects, list) or not projects:
        reasons.append('projects lists each project and its repo, as {"key": "PROJ", "repo": "owner/name"}')
        projects = []
    seen: dict[str, str] = {}
    for entry in projects:
        key, repo = (entry.get("key"), entry.get("repo")) if isinstance(entry, dict) else (None, None)
        if not isinstance(key, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]+", key):
            reasons.append(f"{key!r} is not a project key: the capitals before an issue's number, such as PROJ")
            continue
        if not isinstance(repo, str) or repo.count("/") != 1 or not all(repo.split("/")):
            reasons.append(f"project {key} needs its repo, as owner/name")
            continue
        if repos is not None and repo.lower() not in repos:
            reasons.append(f"project {key}'s repo {repo} is not in the repo registry; add it there first")
        # A ref names its project and nothing else, so a project's issues go to one repo.
        if seen.setdefault(key, repo) != repo:
            reasons.append(f"project {key} is listed for {seen[key]} and {repo}; one project goes to one repo")
    if "issue_type" in jira and (not isinstance(jira["issue_type"], str) or not jira["issue_type"].strip()):
        reasons.append("issue_type is the name of an issue type of the projects, such as Task")
    statuses = jira.get("statuses", {})
    if not isinstance(statuses, dict) or any(
        k not in STATUSES or not isinstance(v, str) or not v.strip() for k, v in statuses.items()
    ):
        reasons.append(f"statuses names the projects' status for any of {', '.join(STATUSES)}")
    return reasons


SPEC = Supported(
    SCHEME,
    "Jira",
    "Issues live in Jira Cloud projects. Each project's issues are worked on in one repo.",
    settings=[
        Setting("site", "Site", required=True, help="The Jira site's address, such as https://example.atlassian.net"),
        Setting(
            "projects",
            "Projects",
            kind="projects",
            required=True,
            help="Each Jira project, by its key (the capitals before an issue's number), and the repo its issues "
            "are worked on in.",
        ),
        Setting("issue_type", "Issue type", help="The type of an issue the planner creates. Task when empty."),
        Setting(
            "statuses",
            "Status names",
            kind="statuses",
            help="The projects' own name for a status, where their workflow does not use the usual ones.",
        ),
    ],
    credentials=["MH_JIRA_EMAIL", "MH_JIRA_TOKEN"],
    check=check_settings,
    build=JiraTracker,
    writes_progress=True,
)
