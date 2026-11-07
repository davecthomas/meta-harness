"""The code host: read a ticket, clone the task's repo, and publish the run's changes as a pull request.

Code-host access stays outside the per-run sandbox. The run activity clones
the repo into the run's workspace on this machine before the per-run sandbox
starts, and the publish activity commits, pushes and opens the pull request
from this machine after pi finishes. The clone's remote is an SSH URL (gh's
git_protocol), and the per-run sandbox holds no SSH key and no code-host
token, so nothing pi runs can push.

A repo is either `owner/name` on GitHub, through the gh CLI and your own gh
login, or the path of a local bare git repo. A local repo makes the whole path
testable with no code host: the branch is pushed to it, and the pull request
is written to runs/<REF>/pull-request.md instead of opened. `make e2e` uses one.

A ticket is `owner/name#number`, a GitHub issue. Its title and body become the
task, a `## Acceptance checks` list in the body becomes the checks, and the
pull request says `Closes owner/name#number`.
"""

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

PR_LABEL = "ai:meta-harness"
COMMIT_NAME = "meta-harness"
COMMIT_EMAIL = "meta-harness@users.noreply.github.com"
TICKET_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)$")
GITHUB_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class CodeHostError(RuntimeError):
    """A git or gh command failed. `retryable` is False when repeating it would fail the same way."""

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


def is_local(repo: str) -> bool:
    return repo.startswith("/")


def check_repo(repo: str) -> None:
    """Raises CodeHostError unless repo is owner/name or an absolute path to a bare git repo."""
    if is_local(repo):
        if not (Path(repo) / "HEAD").is_file():
            raise CodeHostError(f"{repo} is not a bare git repo", retryable=False)
        return
    if not GITHUB_REPO_PATTERN.match(repo):
        raise CodeHostError(f"REPO {repo!r} must be owner/name on GitHub, or the path of a bare git repo", False)


async def read_ticket(ref: str) -> Ticket:
    """A GitHub issue's title, body and acceptance checks. Reads only."""
    match = TICKET_PATTERN.match(ref)
    if not match:
        raise CodeHostError(f"TICKET {ref!r} must be owner/name#number", retryable=False)
    repo, number = match.groups()
    out = await _run("gh", "issue", "view", number, "--repo", repo, "--json", "title,body,state")
    issue = json.loads(out)
    if issue["state"] != "OPEN":
        raise CodeHostError(f"{ref} is {issue['state'].lower()}, so there is nothing to do", retryable=False)
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
        await _run("gh", "repo", "clone", repo, str(workspace), "--", "--quiet")
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
    await _git(workspace, "push", "--quiet", "--force-with-lease", "origin", f"{branch}:{branch}")
    if is_local(repo):
        created = not pr_record.exists()
        pr_record.write_text(f"# {title}\n\nbase: {base}\nhead: {branch} at {commit}\n\n{body}\n", encoding="utf-8")
        return Published(branch, base, commit, files, f"file://{pr_record}", created)
    existing = await _pr_for(repo, branch)
    if existing:
        return Published(branch, base, commit, files, existing, False)
    # The label exists after the first pull request. Creating it again fails, which is fine.
    await _run("gh", "label", "create", PR_LABEL, "--repo", repo, "--color", "5319e7", check=False)
    url = await _run(
        "gh",
        "pr",
        "create",
        "--repo",
        repo,
        "--base",
        base,
        "--head",
        branch,
        "--title",
        title,
        "--body",
        body,
        "--label",
        PR_LABEL,
    )
    return Published(branch, base, commit, files, url.splitlines()[-1], True)


async def _pr_for(repo: str, branch: str) -> str | None:
    out = await _run("gh", "pr", "list", "--repo", repo, "--head", branch, "--state", "open", "--json", "url")
    found = json.loads(out or "[]")
    return found[0]["url"] if found else None


async def _git(workspace: Path, *args: str) -> str:
    return await _run("git", "-C", str(workspace), *args)


async def _run(*args: str, check: bool = True) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError as err:
        raise CodeHostError(f"the {args[0]} command is not installed", retryable=False) from err
    out, err = await process.communicate()
    if check and process.returncode != 0:
        raise CodeHostError(f"{' '.join(args[:3])} failed: {err.decode(errors='replace').strip()}")
    return out.decode(errors="replace").strip()
