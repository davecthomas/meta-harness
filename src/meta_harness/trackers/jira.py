"""Jira Cloud as a tracker. One Jira site holds many projects, and a goal says which project its issues are in.

An issue's ref is `jira:<KEY>`, such as `jira:PROJ-123`. The `trackers` configuration holds the site and the
projects the meta-harness works in:

    "jira": {"site": "https://example.atlassian.net", "default_project": "PROJ",
             "projects": {"PROJ": {"issue_type": "Story", "statuses": {"in_review": "In Code Review"}}}}

A workflow belongs to a project, so what depends on one is set for each project: `issue_type` (the type of an
issue the planner creates; Task when unset) and `statuses` (the project's own name for todo, in_progress,
in_review or done, where its workflow's names are not the usual). `default_project` is required and is one of
`projects`: the project a new goal starts with. A goal that uses Jira names one of the listed projects (its
tracker setting `project`), and its repos as every goal does.

A configuration saved before projects were listed has `issue_type` and `statuses` for the whole site and no
`projects`. It keeps working as it did, any project allowed, until its next save, which needs the projects.

Which repo an issue's work is done in: the goal's repo when it has one. A goal with several reads it from the
issue, a `Repo: owner/name` line in its description or a `repo:<name>` label; an issue that names none of
the goal's repos is held until it does.

Calls sign in as one Jira account: MH_JIRA_EMAIL and MH_JIRA_TOKEN, an API token of that account. They use
REST API version 2, whose descriptions and comments are text (wiki markup), which is what the planner reads an
issue's `## Acceptance checks`, `Paths:` and `Depends on:` lines from.

A status name or issue type the site lacks is caught in two places, and saving the configuration is neither: it
makes no call to Jira. The console refuses the name at entry, where it offers `choices`. The adapter reports it
at first use: the first time it moves an issue to a configured status or creates an issue of a configured type,
it checks the name against the same `choices`, and a name the list lacks gets a warning that names the setting,
the value and the names the site has. The issue then fails to move or be created as before; the warning says
why. A check that cannot reach Jira says nothing and is tried again at the next use.
"""

import asyncio
import logging
import os
import re
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from meta_harness import outbound
from meta_harness import retrying
from meta_harness.run_service.code_host import acceptance_checks, issue_paths
from meta_harness.trackers.base import (
    DONE,
    IN_PROGRESS,
    IN_REVIEW,
    STATUSES,
    TODO,
    Issue,
    Marked,
    Setting,
    Source,
    Supported,
    TrackerError,
    configured_label,
)

log = logging.getLogger(__name__)

SCHEME = "jira"
REF = re.compile(r"^jira:(?P<key>[A-Z][A-Z0-9_]+-\d+)$")
PROJECT = re.compile(r"^[A-Z][A-Z0-9_]+$")
FIELDS = ["summary", "description", "labels", "status", "updated", "reporter", "issuelinks"]
PAGE = 100
TIMEOUT_SECONDS = 30.0
CONNECT_SECONDS = 10.0
# Reused for every call. All of Jira is one address, and the VM reaches it through Cloud NAT, which has a limited
# number of ports for one destination and holds each for a while after its connection closes: a connection per
# call, and a labels read is one call per hundred issues, runs them out, and new connections then time out.
CONNECTIONS = httpx.Limits(max_connections=10, max_keepalive_connections=5, keepalive_expiry=60.0)
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
    # For the whole site, from before projects were listed: what a project that sets none of its own uses.
    issue_type: str = "Task"
    statuses: dict[str, str] = field(default_factory=dict)
    # Each listed project's own issue_type and statuses, by its key.
    projects: dict[str, dict[str, Any]] = field(default_factory=dict)

    def issue_type_of(self, project: str) -> str:
        """The type of an issue the planner creates in the project."""
        return str((self.projects.get(project) or {}).get("issue_type") or self.issue_type)

    def statuses_of(self, project: str) -> dict[str, str]:
        """The project's own names for our statuses."""
        return dict((self.projects.get(project) or {}).get("statuses") or self.statuses)


class Connections:
    """The adapter's HTTP client, one per event loop, shared by every copy bound to a goal."""

    def __init__(self) -> None:
        # What was checked against the site's lists, by project, setting and name: once for every copy.
        self.checked: set[tuple[str, str, str]] = set()
        self.clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient] = (
            weakref.WeakKeyDictionary()
        )

    def client(self, transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if loop not in self.clients:
            self.clients[loop] = httpx.AsyncClient(
                timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=CONNECT_SECONDS),
                limits=CONNECTIONS,
                transport=retrying.async_transport(transport, safe=_only_reads),
            )
        return self.clients[loop]


def _only_reads(request: httpx.Request) -> bool:
    """Jira's issue search is a POST that changes nothing, so it can be repeated like a GET."""
    return request.url.path.endswith("/search/jql")


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
        projects={key: dict(spec or {}) for key, spec in (jira.get("projects") or {}).items()},
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
        label: Callable[[], str] = configured_label,
        marked: Marked | None = None,
        connections: Connections | None = None,
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
        self._assigned: list[Issue] | None = None
        self.label = label
        self.marked = marked or Marked()
        self.connections = connections or Connections()

    def bound(self, source: Source) -> "JiraTracker":
        return JiraTracker(
            self.email, self.token, self.site, self.transport, source, self.label, self.marked, self.connections
        )

    async def mark(self, ref: str) -> None:
        """Adds the meta-harness's label to the issue. Jira makes a label when an issue first carries it, and
        adding one the issue has changes nothing."""
        label = self.label()
        if (ref, label) in self.marked.refs:
            return
        await self._call("PUT", f"/issue/{parse_ref(ref)}", json={"update": {"labels": [{"add": label}]}})
        self.marked.refs.add((ref, label))

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

    async def _check_name(self, project: str, setting: str, value: str) -> None:
        """Warns, the first time a configured name is used, when the site's `choices` for it lack the name."""
        value = value.strip()
        mark = (project, setting, value.lower())
        if not value or mark in self.connections.checked:
            return
        try:
            names = [c["value"] for c in await self.choices(setting, {"project": project})]
        except TrackerError:
            return
        self.connections.checked.add(mark)
        if names and value.lower() not in {n.lower() for n in names}:
            log.warning(
                "Jira project %s: %s %r is not one the site has (it has: %s). An issue will not %s.",
                project,
                setting,
                value,
                ", ".join(names),
                "move to it" if setting == "statuses" else "be created",
            )

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
            response = await self.connections.client(self.transport).request(
                method, f"{site.site}/rest/api/2{path}", auth=(self.email, self.token), **kwargs
            )
        except httpx.HTTPError as err:
            raise TrackerError(
                f"Jira did not answer {method} {path} after several tries ({type(err).__name__}: {err or 'no reply'}). "
                "Try again shortly; Admin, Logs shows each attempt.",
            ) from err
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
            self._open[label] = await self._search("" if label is None else f" AND labels = {_quoted(label)}")
        return self._for_repo(self._open[label], repo)

    async def list_assigned(self, repo: str) -> list[Issue]:
        """The open issues of the goal's project assigned to the Jira account the meta-harness signs in as,
        for a goal that asked to take them (its setting `assigned`); none for any other goal."""
        if not takes_assigned(self.source.settings if self.source else {}):
            return []
        if self._assigned is None:
            self._assigned = await self._search(" AND assignee = currentUser()")
        return self._for_repo(self._assigned, repo)

    def _for_repo(self, issues: list[Issue], repo: str) -> list[Issue]:
        first = bool(self.source and self.source.repos and self.source.repos[0].lower() == repo.lower())
        return [i for i in issues if i.repo.lower() == repo.lower() or (first and not i.repo)]

    async def _search(self, also: str) -> list[Issue]:
        """The goal's project's issues not in a Done status that also match a JQL clause (" AND ...")."""
        jql = f'project = "{self._project()}" AND statusCategory != Done{also}'
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
        outbound.require(title, "an issue's title")
        outbound.require(body, "an issue's body")
        several = self.source is not None and len(self.source.repos) > 1
        await self._check_name(self._project(), "issue_type", self._site().issue_type_of(self._project()))
        made = await self._call(
            "POST",
            "/issue",
            json={
                "fields": {
                    "project": {"key": self._project()},
                    "summary": title,
                    "description": f"{body.rstrip()}\n\nRepo: {repo}\n" if several else body,
                    "issuetype": {"name": self._site().issue_type_of(self._project())},
                    "labels": list(dict.fromkeys([*labels, self.label()])),
                }
            },
        )
        self._open.clear()
        self.marked.refs.add((ref_for(made["key"]), self.label()))
        return await self.read(ref_for(made["key"]))

    async def comment(self, ref: str, body: str) -> str:
        outbound.require(body, "a comment on an issue")
        key = parse_ref(ref)
        await self.mark(ref)
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
        project = key.rsplit("-", 1)[0]
        await self._check_name(project, "statuses", site.statuses_of(project).get(status, ""))
        named = site.statuses_of(project).get(status, "").strip().lower()
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
                f"status for {status} under Configuration, Issue trackers, Jira, in the issue's project, or move the "
                "issue in Jira.",
                retryable=False,
            )
        await self.mark(ref)
        await self._call("POST", f"/issue/{key}/transitions", json={"transition": {"id": chosen[0]["id"]}})
        # What was read as open may now be done.
        self._open.clear()
        return True

    async def link_pull_request(self, ref: str, pr_url: str, title: str) -> None:
        """Adds the pull request to the issue's links. Its address is the link's id, so adding it again
        changes the one link."""
        key = parse_ref(ref)
        link = {"globalId": pr_url, "object": {"url": pr_url, "title": title or pr_url}}
        await self.mark(ref)
        await self._call("POST", f"/issue/{key}/remotelink", json=link)

    async def choices(self, setting: str, scope: dict[str, str] | None = None) -> list[dict[str, str]]:
        """What the console offers for a setting, so a name is picked and not typed.

        - `projects`: every project of the site the Jira account sees, by name, for the list of those the
          meta-harness works in.
        - `project`: the listed projects, for a goal to name one; every project while none is listed.
        - `issue_type` and `statuses`, with scope's `project`: that project's own issue types (but the sub-task
          ones, which need a parent) and the status names of its workflows. A workflow belongs to a project, so
          another project's statuses move none of its issues, and they are not offered. Empty with no project.
        """
        project = str((scope or {}).get("project") or "").strip().upper()
        if setting in ("statuses", "issue_type"):
            if not PROJECT.match(project):
                return []
            # Each of the project's issue types, with the statuses of that type's workflow.
            types = [t for t in await self._call("GET", f"/project/{project}/statuses") or [] if not t.get("subtask")]
            found = types if setting == "issue_type" else [status for t in types for status in t.get("statuses") or []]
            # Once each, whatever its capitals: two issue types' workflows share most of their statuses.
            names: dict[str, str] = {}
            for name in sorted({str(entry.get("name") or "").strip() for entry in found} - {""}):
                names.setdefault(name.lower(), name)
            return [{"value": name, "label": name} for _, name in sorted(names.items())]
        if setting not in ("project", "projects"):
            return []
        listed = set(self.site().projects) if setting == "project" else set()
        found: list[dict[str, str]] = []
        start = 0
        while True:
            try:
                page = await self._call("GET", "/project/search", params={"startAt": start, "maxResults": PAGE})
            except TrackerError:
                if not listed:
                    raise
                # Jira gives the projects' names. Their keys are the configuration's, so a goal can still be made.
                return [{"value": key, "label": key} for key in sorted(listed)]
            values = page.get("values") or []
            found += [{"value": p["key"], "label": f"{p.get('name') or p['key']} ({p['key']})"} for p in values]
            start += len(values)
            if page.get("isLast", True) or not values:
                found = [choice for choice in found if not listed or choice["value"] in listed]
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


def _check_names(where: str, spec: dict[str, Any]) -> list[str]:
    """Why an issue type and status names cannot be used, for the site or one of its projects."""
    reasons = []
    if "issue_type" in spec and (not isinstance(spec["issue_type"], str) or not spec["issue_type"].strip()):
        reasons.append(f"{where}issue_type is the name of one of the project's issue types, such as Task")
    statuses = spec.get("statuses", {})
    if not isinstance(statuses, dict) or any(
        k not in STATUSES or not isinstance(v, str) or not v.strip() for k, v in statuses.items()
    ):
        reasons.append(f"{where}statuses names the project's status for any of {', '.join(STATUSES)}")
    return reasons


def check_settings(jira: dict[str, Any], repos: set[str] | None) -> list[str]:
    """Why the site's settings cannot be used; empty when they can."""
    reasons = []
    site = jira.get("site")
    if not isinstance(site, str) or not re.fullmatch(r"https://[a-z0-9.-]+\.[a-z]{2,}/?", site):
        reasons.append("site is the Jira site's address, such as https://example.atlassian.net")
    # For the whole site, from before projects were listed; still read, so an earlier version can be rolled back to.
    reasons += _check_names("", jira)
    projects = jira.get("projects", {})
    if not isinstance(projects, dict) or any(
        not isinstance(key, str) or not PROJECT.match(key) or not isinstance(spec, dict)
        for key, spec in projects.items()
    ):
        reasons.append("projects lists the Jira projects the meta-harness works in, each by its key, such as PROJ")
        projects = {}
    for key, spec in projects.items():
        reasons += _check_names(f"project {key}: ", spec)
    chosen = jira.get("default_project")
    if not chosen or chosen not in projects:
        listed = ", ".join(sorted(projects)) or "none yet: add one under Projects first"
        reasons.append(
            f"default_project is required: the project a new goal starts with, one of the projects listed ({listed})"
        )
    return reasons


def goal_defaults(jira: dict[str, Any] | None) -> dict[str, str]:
    """What a new goal on Jira starts with: the default project."""
    chosen = (jira or {}).get("default_project")
    return {"project": chosen} if isinstance(chosen, str) and chosen else {}


def takes_assigned(settings: dict[str, Any]) -> bool:
    """Whether a goal with these settings takes the issues assigned to the meta-harness's Jira account."""
    return settings.get("assigned") == "yes"


def check_goal(settings: dict[str, Any], site: dict[str, Any] | None = None) -> list[str]:
    """Why a goal's Jira settings cannot be used; empty when they can. site: Jira's own settings to check the
    project against, the active configuration's when None. A goal names one of the projects listed there; a
    configuration from before projects were listed allows any."""
    if site is None:
        from meta_harness.trackers import registry

        site = registry.settings(SCHEME) or {}
    reasons = []
    project = settings.get("project")
    listed = site.get("projects") if isinstance(site.get("projects"), dict) else {}
    if not isinstance(project, str) or not PROJECT.match(project):
        reasons.append(
            "a goal that uses Jira names its project by its key, the capitals before an issue's number, such as PROJ"
        )
    elif listed and project not in listed:
        reasons.append(
            f"{project} is not one of the Jira projects the meta-harness works in ({', '.join(sorted(listed))}): "
            "choose one of those, or add it under Configuration, Issue trackers, Jira, Projects"
        )
    if settings.get("assigned", "") not in ("", "yes"):
        reasons.append('assigned is "yes" for a goal that takes the issues assigned to the meta-harness, else left out')
    return reasons


SPEC = Supported(
    SCHEME,
    "Jira",
    "Issues live in the projects of one Jira Cloud site. Each goal says which of the listed projects its issues are in.",
    settings=[
        Setting("site", "Site", required=True, help="The Jira site's address, such as https://example.atlassian.net"),
        Setting(
            "projects",
            "Projects",
            kind="entries",
            entry="project",
            required=True,
            help="The Jira projects the meta-harness works in. A goal names one of them. A workflow belongs to a "
            "project, so each has its own issue type and status names.",
            settings=[
                Setting(
                    "issue_type",
                    "Issue type",
                    kind="choice",
                    help="The type of an issue the planner creates in the project. Task when empty.",
                ),
                Setting(
                    "statuses",
                    "Status names",
                    kind="statuses",
                    help="The project's own name for a status, where its workflow does not use the usual ones.",
                ),
            ],
        ),
        Setting(
            "default_project",
            "Default project",
            kind="entry_of",
            of="projects",
            required=True,
            help="The project a new goal starts with. A goal may name another of the projects listed.",
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
        ),
        Setting(
            "assigned",
            "Take issues assigned to the meta-harness",
            kind="switch",
            help="On: the goal also takes every open issue of the project that is assigned, in Jira, to the "
            "account the meta-harness signs in as, whatever its labels. Assigning an issue there hands it over.",
        ),
    ],
    check_goal=check_goal,
    goal_defaults=goal_defaults,
    takes_assigned=takes_assigned,
)
