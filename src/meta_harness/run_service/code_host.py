"""The code host: read a ticket, clone the task's repo, and publish the run's changes as a pull request.

Code-host access stays outside the per-run sandbox. The run activity clones
the repo into the run's workspace on this machine before the per-run sandbox
starts, and the publish activity commits, pushes and opens the pull request
from this machine after pi finishes.

A repo is `owner/name` on GitHub, reached through the owner's GitHub App
(meta_harness.github_app), or the path of a local bare git repo. The clone
uses an installation token limited to that repo with `contents: read`; a push
uses one with `contents: write`. Each token reaches git through the
environment of that one git command, so neither the workspace's .git/config
nor the command line ever holds it, and the per-run sandbox, which mounts the
workspace, never sees one. A local repo makes the whole path testable with no
code host: the branch is pushed to it, and the pull request is written to
runs/<REF>/pull-request.md instead of opened. `make e2e` uses one.

A ticket is `owner/name#number`, a GitHub issue. Its title and body become the
task, a `## Acceptance checks` list in the body becomes the checks, and the
pull request says `Closes owner/name#number`.
"""

import asyncio
import os
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from meta_harness.github_app import GitHubAppError, GitHubApps, git_auth_env

PR_LABEL = "ai:meta-harness"
COMMIT_NAME = "meta-harness"
COMMIT_EMAIL = "meta-harness@users.noreply.github.com"
TICKET_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)$")
GITHUB_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

READ_CODE = {"contents": "read"}
WRITE_CODE = {"contents": "write"}
READ_ISSUES = {"issues": "read"}
OPEN_PULL_REQUEST = {"pull_requests": "write", "issues": "write"}


class CodeHostError(RuntimeError):
    """A git command or code-host call failed. `retryable` is False when repeating it would fail the same way."""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Ticket:
    ref: str
    repo: str
    title: str
    body: str
    checks: list[str]


@dataclass
class Published:
    branch: str
    base: str
    commit: str
    files: list[str]
    pr_url: str
    created: bool


@cache
def apps() -> GitHubApps:
    """One GitHubApps per process, so its installation tokens are cached across activities."""
    return GitHubApps()


def is_local(repo: str) -> bool:
    return repo.startswith("/")


def check_repo(repo: str) -> None:
    """Raises CodeHostError unless repo is owner/name with a GitHub App, or a local bare git repo."""
    if is_local(repo):
        if not (Path(repo) / "HEAD").is_file():
            raise CodeHostError(f"{repo} is not a bare git repo", retryable=False)
        return
    if not GITHUB_REPO_PATTERN.match(repo):
        raise CodeHostError(f"REPO {repo!r} must be owner/name on GitHub, or the path of a bare git repo", False)
    try:
        apps().app_for(repo)
    except GitHubAppError as err:
        raise CodeHostError(str(err), retryable=False) from err


async def _github(repo: str, permissions: dict[str, str], method: str, path: str, **kwargs):
    try:
        return await apps().call(repo, permissions, method, path, **kwargs)
    except GitHubAppError as err:
        raise CodeHostError(str(err), retryable=err.retryable) from err


async def _token(repo: str, permissions: dict[str, str]) -> str:
    try:
        return await apps().token(repo, permissions)
    except GitHubAppError as err:
        raise CodeHostError(str(err), retryable=err.retryable) from err


async def read_ticket(ref: str) -> Ticket:
    """A GitHub issue's title, body and acceptance checks. Reads only."""
    match = TICKET_PATTERN.match(ref)
    if not match:
        raise CodeHostError(f"TICKET {ref!r} must be owner/name#number", retryable=False)
    repo, number = match.groups()
    issue = await _github(repo, READ_ISSUES, "GET", f"/repos/{repo}/issues/{number}")
    if issue["state"] != "open":
        raise CodeHostError(f"{ref} is {issue['state']}, so there is nothing to do", retryable=False)
    body = issue["body"] or ""
    return Ticket(ref=ref, repo=repo, title=issue["title"], body=body, checks=acceptance_checks(body))


def acceptance_checks(body: str) -> list[str]:
    """The list items under a `## Acceptance checks` heading, with backticks removed."""
    checks: list[str] = []
    in_section = False
    for line in body.splitlines():
        if line.startswith("#"):
            in_section = line.lstrip("#").strip().lower() == "acceptance checks"
            continue
        item = re.match(r"^\s*[-*]\s+(?:\[[ xX]\]\s+)?(.+)$", line)
        if in_section and item:
            checks.append(item.group(1).strip().strip("`"))
    return checks


async def clone(repo: str, workspace: Path) -> str:
    """Clones repo's default branch into workspace, unless an earlier attempt did. Returns the branch name."""
    if (workspace / ".git").is_dir():
        return await _git(workspace, "rev-parse", "--abbrev-ref", "HEAD")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    if is_local(repo):
        await _run("git", "clone", "--quiet", repo, str(workspace))
    else:
        env = git_auth_env(await _token(repo, READ_CODE))
        await _run("git", "clone", "--quiet", f"https://github.com/{repo}.git", str(workspace), env=env)
    return await _git(workspace, "rev-parse", "--abbrev-ref", "HEAD")


async def publish(
    *, repo: str, workspace: Path, branch: str, title: str, body: str, commit_message: str, pr_record: Path
) -> Published | None:
    """Commits whatever pi changed onto a new branch, pushes it, and opens the pull request.

    Returns None when pi changed nothing. A retried attempt finds the branch
    and the pull request the earlier attempt made, and makes neither again.
    """
    base = await _git(workspace, "rev-parse", "--abbrev-ref", "origin/HEAD")
    base = base.removeprefix("origin/")
    current = await _git(workspace, "rev-parse", "--abbrev-ref", "HEAD")
    if current != branch:
        await _git(workspace, "checkout", "--quiet", "-B", branch)
    await _git(workspace, "add", "--all")
    if await _git(workspace, "status", "--porcelain"):
        await _git(
            workspace,
            "-c",
            f"user.name={COMMIT_NAME}",
            "-c",
            f"user.email={COMMIT_EMAIL}",
            "commit",
            "--quiet",
            "--message",
            commit_message,
        )
    files = [f for f in (await _git(workspace, "diff", "--name-only", f"origin/{base}...HEAD")).splitlines() if f]
    if not files:
        return None
    commit = await _git(workspace, "rev-parse", "HEAD")
    env = None if is_local(repo) else git_auth_env(await _token(repo, WRITE_CODE))
    await _git(workspace, "push", "--quiet", "--force-with-lease", "origin", f"{branch}:{branch}", env=env)
    if is_local(repo):
        created = not pr_record.exists()
        pr_record.write_text(f"# {title}\n\nbase: {base}\nhead: {branch} at {commit}\n\n{body}\n", encoding="utf-8")
        return Published(branch, base, commit, files, f"file://{pr_record}", created)
    owner = repo.split("/", 1)[0]
    existing = await _github(
        repo, OPEN_PULL_REQUEST, "GET", f"/repos/{repo}/pulls", params={"head": f"{owner}:{branch}", "state": "open"}
    )
    if existing:
        return Published(branch, base, commit, files, existing[0]["html_url"], False)
    pull = await _github(
        repo,
        OPEN_PULL_REQUEST,
        "POST",
        f"/repos/{repo}/pulls",
        json={"title": title, "body": body, "head": branch, "base": base},
    )
    # Adding a label that does not exist yet creates it.
    await _github(
        repo, OPEN_PULL_REQUEST, "POST", f"/repos/{repo}/issues/{pull['number']}/labels", json={"labels": [PR_LABEL]}
    )
    return Published(branch, base, commit, files, pull["html_url"], True)


async def _git(workspace: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return await _run("git", "-C", str(workspace), *args, env=env)


async def _run(*args: str, check: bool = True, env: dict[str, str] | None = None) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **env} if env else None,
        )
    except FileNotFoundError as err:
        raise CodeHostError(f"the {args[0]} command is not installed", retryable=False) from err
    out, err = await process.communicate()
    if check and process.returncode != 0:
        raise CodeHostError(f"{' '.join(args[:3])} failed: {err.decode(errors='replace').strip()}")
    return out.decode(errors="replace").strip()
