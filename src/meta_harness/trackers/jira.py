"""Jira Cloud as a tracker. One Jira site holds many projects, and a goal says which project its issues are in.

An issue's ref is `jira:<KEY>`, such as `jira:PROJ-123`. The `trackers` configuration holds what is true of
the whole site: `"jira": {"site": "https://example.atlassian.net"}`, with optional `issue_type` (the type of an
issue the planner creates; Task when unset) and `statuses` (the site's own name for todo, in_progress,
in_review or done, where its workflows' names are not the usual). A goal that uses Jira names its project
(its tracker setting `project`), and its repos as every goal does.

Which repo an issue's work is done in: the goal's repo when it has one. A goal with several reads it from the
issue, a `Repo: owner/name` line in its description or a `repo:<name>` label; an issue that names none of
the goal's repos is held until it does.

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
    Source,
    Supported,
    TrackerError,
)

SCHEME = "jira"
REF = re.compile(r"^jira:(?P<key>[A-Z][A-Z0-9_]+-\d+)$")
PROJECT = re.compile(r"^[A-Z][A-Z0-9_]+$")
FIELDS = ["summary", "description", "labels", "status", "updated", "reporter", "issuelinks"]
PAGE = 100
TIMEOUT_SECONDS = 30.0
# "Depends on: PROJ-12, PROJ-13" lines in a description, beside the issue's own "is blocked by" links.
DEPENDS_LINE = re.compile(r"^\s*depends on:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
DEPENDS_KEY = re.compile(r"\b[A-Z][A-Z0-9_]+-\d+\b")
# "Repo: owner/name" in a description, and a "repo:<name>" label: which of the goal's repos the issue is for.
REPO_LINE = re.compile(r"^\s*repo:\s*`?([\w.-]+(?:/[\w.-]+)?)`?\s*$", re.IGNORECASE | re.MULTILINE)
REPO_LABEL = re.compile(r"^repo:([\w.-]+(?:/[\w.-]+)?)$", re.IGNORECASE)
# What a workflow most often calls each status of ours, most usual first. In review is any status with "review"
# in its name.
USUAL = {
    TODO: ("to do", "todo", "open", "backlog"),
    IN_PROGRESS: ("in progress", "in dev", "in development", "doing"),
    DONE: ("done", "closed", "resolved", "complete", "completed"),
}
# Jira's three status categories, which every workflow's statuses belong to, for each status of ours.
CATEGORY = {TODO: "new", IN_PROGRESS: "indeterminate", IN_REVIEW: "indeterminate", DONE: "done"}


@dataclass(frozen=True)
class Site:
    """The Jira site, as the trackers configuration describes it."""

    site: str
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


def configured_site() -> Site:
    """The site from the trackers configuration; an empty address when Jira is not set up."""
    from meta_harness.trackers import registry

    jira = registry.settings(SCHEME) or {}
    return Site(
        site=str(jira.get("site", "")).rstrip("/"),
        issue_type=str(jira.get("issue_type") or "Task"),
        statuses=dict(jira.get("statuses") or {}),
    )


def repo_for(repos: tuple[str, ...], body: str, labels: list[str]) -> str:
    """Which of the goal's repos the issue's work is done in: the one repo of a goal that has one; else the one
    its `Repo:` line or `repo:` label names, by owner/name or by name alone. "" when it names none of them."""
    if len(repos) == 1:
        return repos[0]
    named = REPO_LINE.findall(body or "") + [m[1] for label in labels if (m := REPO_LABEL.match(label))]
    for name in named:
        found = [r for r in repos if name.lower() in (r.lower(), r.lower().split("/")[-1])]
        if len(found) == 1:
            return found[0]
    return ""


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
        site: Callable[[], Site] = configured_site,
        transport: httpx.AsyncBaseTransport | None = None,
        source: Source | None = None,
    ) -> None:
        self.email = email if email is not None else os.environ.get("MH_JIRA_EMAIL", "")
        self.token = token if token is not None else os.environ.get("MH_JIRA_TOKEN", "")
        self.site = site
        self.transport = transport
        # The goal this adapter reads for: its project and repos. None: no goal, so only calls that name an
        # issue by its ref can be answered.
        self.source = source
        # One search of the project for each label, shared by the calls that ask for each of the goal's repos.
        self._open: dict[str | None, list[Issue]] = {}

    def bound(self, source: Source) -> "JiraTracker":
        return JiraTracker(self.email, self.token, self.site, self.transport, source)

    @property
    def connected(self) -> bool:
        """Whether the Jira account's sign-in is set."""
        return bool(self.email and self.token)

    def _site(self) -> Site:
        site = self.site()
        if not site.site.startswith("https://"):
            raise TrackerError(
                "Jira's site is not set: set it under Configuration, Issue trackers, Jira, as "
                "https://<name>.atlassian.net",
                retryable=False,
            )
        return site

    def _project(self) -> str:
        """The project of the goal this adapter reads for."""
        project = str((self.source.settings if self.source else {}).get("project") or "")
        if not PROJECT.match(project):
            raise TrackerError(
                "no Jira project is named: a goal that uses Jira names its project, by its key (the capitals "
                "before an issue's number, such as PROJ). Choose it when creating the goal.",
                retryable=False,
            )
        return project

    def repo_of(self, ref: str) -> str:
        """A repo of the goal's to hold the issue under until it is read, which settles the one its work is
        done in.

        Raises:
            ValueError: The ref is not a Jira issue's, or is in another project than the goal's.
        """
        key = parse_ref(ref)
        if self.source is None or not self.source.repos:
            raise ValueError(f"{ref} names no repo: a Jira issue's repo is its goal's")
        try:
            project = self._project()
        except TrackerError as err:
            raise ValueError(str(err)) from err
        if key.rsplit("-", 1)[0] != project:
            raise ValueError(f"{key} is not in {project}, the goal's Jira project")
        return self.source.repos[0]

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        site = self._site()
        if not self.email or not self.token:
            raise TrackerError(
                "Jira is not signed in: add the secrets MH_JIRA_EMAIL (the Jira account) and MH_JIRA_TOKEN (an "
                "API token of that account) to the deployment, then deploy",
                retryable=False,
            )
        try:
            async with httpx.AsyncClient(
                base_url=site.site, auth=(self.email, self.token), timeout=TIMEOUT_SECONDS, transport=self.transport
            ) as http:
                response = await http.request(method, f"/rest/api/2{path}", **kwargs)
        except httpx.HTTPError as err:
            raise TrackerError(f"Jira did not answer {method} {path}: {type(err).__name__}: {err}") from err
        if response.status_code >= 400:
            hint = {
                401: " Check MH_JIRA_EMAIL and MH_JIRA_TOKEN: the token may have expired or been revoked.",
                403: " The Jira account needs that permission in the project.",
                404: " The issue or project may not exist, or the Jira account may not see it.",
            }.get(response.status_code, "")
            retryable = response.status_code >= 500 or response.status_code == 429
            raise TrackerError(
                f"Jira refused {method} {path} ({response.status_code}): {_why(response)}.{hint}",
                retryable=retryable,
                status=response.status_code,
            )
        return response.json() if response.content else {}

    def _issue(self, data: dict[str, Any]) -> Issue:
        fields = data["fields"]
        body = markdown(fields.get("description") or "")
        labels = list(fields.get("labels") or [])
        done = ((fields.get("status") or {}).get("statusCategory") or {}).get("key") == "done"
        return Issue(
            ref=ref_for(data["key"]),
            # Empty with no goal to say, or when a goal with several repos has an issue that names none.
            repo=repo_for(self.source.repos, body, labels) if self.source else "",
            number=int(data["key"].rsplit("-", 1)[1]),
            title=fields.get("summary") or "",
            body=body,
            labels=labels,
            state="closed" if done else "open",
            depends_on=depends_on(data, body),
            checks=acceptance_checks(body),
            updated_at=fields.get("updated") or "",
            paths=issue_paths(body),
            author=(fields.get("reporter") or {}).get("displayName") or "",
        )

    async def list_open(self, repo: str, label: str | None) -> list[Issue]:
        """The open issues of the goal's project that are for the repo. One that names none of the goal's
        repos comes with the first repo's, its `repo` empty, so it is seen once and held."""
        if label not in self._open:
            self._open[label] = await self._search(label)
        first = bool(self.source and self.source.repos and self.source.repos[0].lower() == repo.lower())
        return [i for i in self._open[label] if i.repo.lower() == repo.lower() or (first and not i.repo)]

    async def _search(self, label: str | None) -> list[Issue]:
        jql = f'project = "{self._project()}" AND statusCategory != Done'
        if label is not None:
            jql += f" AND labels = {_quoted(label)}"
        issues: list[Issue] = []
        token: str | None = None
        while True:
            ask = {"jql": f"{jql} ORDER BY created ASC", "fields": FIELDS, "maxResults": PAGE}
            page = await self._call("POST", "/search/jql", json={**ask, **({"nextPageToken": token} if token else {})})
            issues += [self._issue(item) for item in page.get("issues") or []]
            token = page.get("nextPageToken")
            if not token or page.get("isLast"):
                return issues

    async def read(self, ref: str) -> Issue:
        key = parse_ref(ref)
        return self._issue(await self._call("GET", f"/issue/{key}", params={"fields": ",".join(FIELDS)}))

    async def ensure_label(self, repo: str, label: str) -> None:
        """Jira makes a label when an issue first carries it, so there is nothing to create; a label Jira could
        not hold is refused here, before any issue is made."""
        if not label or re.search(r"\s", label):
            raise TrackerError(
                f"a Jira label has no spaces, and the goal's label is {label!r}: change the goal's label to one "
                "without spaces",
                retryable=False,
            )

    async def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> Issue:
        """Creates the issue in the goal's project. For a goal with several repos its description says which
        one it is for, the way the planner reads it back."""
        several = self.source is not None and len(self.source.repos) > 1
        made = await self._call(
            "POST",
            "/issue",
            json={
                "fields": {
                    "project": {"key": self._project()},
                    "summary": title,
                    "description": f"{body.rstrip()}\n\nRepo: {repo}\n" if several else body,
                    "issuetype": {"name": self._site().issue_type},
                    "labels": labels,
                }
            },
        )
        self._open.clear()
        return await self.read(ref_for(made["key"]))

    async def comment(self, ref: str, body: str) -> str:
        key = parse_ref(ref)
        made = await self._call("POST", f"/issue/{key}/comment", json={"body": body})
        return f"{self._site().site}/browse/{key}?focusedCommentId={made['id']}" if made.get("id") else ""

    async def set_status(self, ref: str, status: str) -> bool:
        """Moves the issue through its workflow: to the status the site's settings name for ours; else to one
        with the usual name for it (In Progress, Done, any name with "review"); else to one of the matching
        Jira category. An issue already there stays.

        Raises:
            TrackerError: The issue's workflow has no move to such a status from where it is.
        """
        if status not in STATUSES:
            raise ValueError(f"{status!r} is not one of {', '.join(STATUSES)}")
        key = parse_ref(ref)
        site = self._site()
        now = (await self._call("GET", f"/issue/{key}", params={"fields": "status"}))["fields"]["status"]
        named = site.statuses.get(status, "").strip().lower()
        category = (now.get("statusCategory") or {}).get("key")
        here = now.get("name", "").lower()
        if named and here == named:
            return False
        moves = (await self._call("GET", f"/issue/{key}/transitions")).get("transitions") or []

        def to(move: dict) -> tuple[str, str]:
            target = move.get("to") or {}
            return target.get("name", "").lower(), (target.get("statusCategory") or {}).get("key", "")

        if named:
            chosen = [m for m in moves if to(m)[0] == named]
        else:
            # The status's usual name first, then Jira's category. A workflow's categories do not always say
            # what its statuses mean: one may class "Ready for Development" as in progress, and "In Code Review"
            # as not started.
            def is_it(name: str) -> bool:
                return "review" in name if status == IN_REVIEW else name in USUAL[status]

            if is_it(here):
                return False
            order = {name: i for i, name in enumerate(USUAL.get(status, ()))}
            chosen = sorted((m for m in moves if is_it(to(m)[0])), key=lambda m: order.get(to(m)[0], 0))
            if not chosen:
                # No status of the usual names: one of the right category will do, and an issue already in it stays.
                if category == CATEGORY[status] and (status == IN_REVIEW or "review" not in here):
                    return False
                if status != IN_REVIEW:
                    chosen = [m for m in moves if to(m)[1] == CATEGORY[status] and "review" not in to(m)[0]]
                    chosen = chosen or [m for m in moves if to(m)[1] == CATEGORY[status]]
        if not chosen:
            can = ", ".join(sorted(m.get("to", {}).get("name", "?") for m in moves)) or "nowhere"
            raise TrackerError(
                f"{key} is {now.get('name')!r} and its workflow has no move to "
                f"{named or status.replace('_', ' ')!r} from there (it can go to: {can}). Name the site's "
                f"status for {status} under Configuration, Issue trackers, Jira, or move the issue in Jira.",
                retryable=False,
            )
        await self._call("POST", f"/issue/{key}/transitions", json={"transition": {"id": chosen[0]["id"]}})
        # What was read as open may now be done.
        self._open.clear()
        return True

    async def link_pull_request(self, ref: str, pr_url: str, title: str) -> None:
        """Adds the pull request to the issue's links. Its address is the link's id, so adding it again
        changes the one link."""
        key = parse_ref(ref)
        link = {"globalId": pr_url, "object": {"url": pr_url, "title": title or pr_url}}
        await self._call("POST", f"/issue/{key}/remotelink", json=link)

    async def choices(self, setting: str) -> list[dict[str, str]]:
        """What a goal may choose for one of its settings: for `project`, the site's projects the Jira account
        sees, by name."""
        if setting != "project":
            return []
        found: list[dict[str, str]] = []
        start = 0
        while True:
            page = await self._call("GET", "/project/search", params={"startAt": start, "maxResults": PAGE})
            values = page.get("values") or []
            found += [{"value": p["key"], "label": f"{p.get('name') or p['key']} ({p['key']})"} for p in values]
            start += len(values)
            if page.get("isLast", True) or not values:
                return sorted(found, key=lambda choice: choice["label"].lower())

    def issue_url_prefix(self) -> str | None:
        """The start of every issue's address, which its key follows; None until the site is set."""
        site = self.site().site
        return f"{site}/browse/" if site.startswith("https://") else None

    def url(self, ref: str) -> str | None:
        match = REF.match(ref)
        prefix = self.issue_url_prefix()
        return f"{prefix}{match['key']}" if match and prefix else None


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
    """Why the site's settings cannot be used; empty when they can."""
    reasons = []
    site = jira.get("site")
    if not isinstance(site, str) or not re.fullmatch(r"https://[a-z0-9.-]+\.[a-z]{2,}/?", site):
        reasons.append("site is the Jira site's address, such as https://example.atlassian.net")
    if "issue_type" in jira and (not isinstance(jira["issue_type"], str) or not jira["issue_type"].strip()):
        reasons.append("issue_type is the name of an issue type of the site's projects, such as Task")
    statuses = jira.get("statuses", {})
    if not isinstance(statuses, dict) or any(
        k not in STATUSES or not isinstance(v, str) or not v.strip() for k, v in statuses.items()
    ):
        reasons.append(f"statuses names the site's status for any of {', '.join(STATUSES)}")
    return reasons


def check_goal(settings: dict[str, Any]) -> list[str]:
    """Why a goal's Jira settings cannot be used; empty when they can."""
    project = settings.get("project")
    if not isinstance(project, str) or not PROJECT.match(project):
        return [
            "a goal that uses Jira names its project by its key, the capitals before an issue's number, such as PROJ"
        ]
    return []


SPEC = Supported(
    SCHEME,
    "Jira",
    "Issues live in the projects of one Jira Cloud site. Each goal says which project its issues are in.",
    settings=[
        Setting("site", "Site", required=True, help="The Jira site's address, such as https://example.atlassian.net"),
        Setting("issue_type", "Issue type", help="The type of an issue the planner creates. Task when empty."),
        Setting(
            "statuses",
            "Status names",
            kind="statuses",
            help="The site's own name for a status, where its workflows do not use the usual ones.",
        ),
    ],
    credentials=["MH_JIRA_EMAIL", "MH_JIRA_TOKEN"],
    check=check_settings,
    build=JiraTracker,
    writes_progress=True,
    goal_settings=[
        Setting(
            "project",
            "Jira project",
            kind="choice",
            required=True,
            help="The project the goal's issues are in. One site has many projects; each goal names one.",
        )
    ],
    check_goal=check_goal,
)
