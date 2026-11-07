"""The issue tracker adapter, GitHub first. The planner is the only component that calls it.

An issue's ref names its tracker: `github:<owner>/<repo>#<number>`. The
adapter reads through the repo owner's GitHub App with `issues: read`.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

from meta_harness.github_app import GitHubApps
from meta_harness.run_service.code_host import acceptance_checks

READ_ISSUES = {"issues": "read"}
REF = re.compile(r"^github:(?P<repo>[\w.-]+/[\w.-]+)#(?P<number>\d+)$")
# "Depends on: #12, owner/repo#13" lines in an issue body.
DEPENDS_LINE = re.compile(r"^\s*depends on:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
DEPENDS_ITEM = re.compile(r"(?:(?P<repo>[\w.-]+/[\w.-]+))?#(?P<number>\d+)")


@dataclass
class Issue:
    ref: str
    repo: str
    number: int
    title: str
    body: str
    labels: list[str]
    state: str
    depends_on: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)


class Tracker(Protocol):
    async def list_open(self, repo: str, label: str) -> list[Issue]: ...

    async def read(self, ref: str) -> Issue: ...


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
    )


class GitHubTracker:
    def __init__(self, apps: GitHubApps | None = None) -> None:
        self.apps = apps or GitHubApps()

    async def list_open(self, repo: str, label: str) -> list[Issue]:
        issues: list[Issue] = []
        page = 1
        while True:
            batch = await self.apps.call(
                repo,
                READ_ISSUES,
                "GET",
                f"/repos/{repo}/issues",
                params={"labels": label, "state": "open", "per_page": 100, "page": page},
            )
            # The issues endpoint lists pull requests too.
            issues += [_issue(repo, item) for item in batch if "pull_request" not in item]
            if len(batch) < 100:
                return issues
            page += 1

    async def read(self, ref: str) -> Issue:
        repo, number = parse_ref(ref)
        return _issue(repo, await self.apps.call(repo, READ_ISSUES, "GET", f"/repos/{repo}/issues/{number}"))
