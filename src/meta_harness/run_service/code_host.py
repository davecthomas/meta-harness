"""The code host: read a ticket, clone the task's repo, and publish the run's changes as a pull request.

Code-host access stays outside the per-run sandbox. The run activity clones
the repo into the run's workspace on this machine before the per-run sandbox
starts, and the publish activity commits, pushes and opens the pull request
from this machine after pi finishes.

A repo is `owner/name` on GitHub, reached through the owner's GitHub App
(meta_harness.github_app), or the path of a local bare git repo. The clone
uses an installation token limited to that repo with `contents: read`; a push
uses one with `contents: write`. Each token reaches git through the
environment of that one git command, so neither the git directory's config
nor the command line ever holds it, and the per-run sandbox, which mounts the
workspace, never sees one.

The git directory lives outside the workspace and the sandbox mounts it
read-only (sandbox.git_dir_for). Every host git command names that
directory and the work tree outright. So nothing pi writes in the workspace,
a hook, a `.git` file pointing elsewhere, a `core.fsmonitor` or
`core.hooksPath` setting, runs on the host when the publish activity commits
and pushes. A push to a local repo does run that repo's own hooks on the
host, so a local path is taken only when the deployment vouches for it
(meta_harness.local_repos). A local repo makes the whole path testable with no
code host: the branch is pushed to it, and the pull request is written to
runs/<REF>/pull-request.md instead of opened. `make e2e` uses one.

A ticket is `owner/name#number`, a GitHub issue. Its title and body become the
task, a `## Acceptance checks` list in the body becomes the checks, and the
pull request says `Closes owner/name#number`.
"""

import asyncio
import os
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

from meta_harness import acting, local_repos, outbound
from meta_harness.github_app import GitHubAppError, GitHubApps, git_auth_env
from meta_harness.run_service.provenance import Provenance, commit_message, stamp

PR_LABEL = "ai:meta-harness"
COMMIT_NAME = "meta-harness"
COMMIT_EMAIL = "meta-harness@users.noreply.github.com"
TICKET_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)$")
GITHUB_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

READ_CODE = {"contents": "read"}
WRITE_CODE = {"contents": "write"}
READ_ISSUES = {"issues": "read"}
OPEN_PULL_REQUEST = {"pull_requests": "write", "issues": "write"}
READ_CHECKS = {"checks": "read", "statuses": "read"}
REVIEW = {"pull_requests": "write"}
READ_PULL_REQUEST = {"pull_requests": "read"}
READ_ACTIONS = {"actions": "read"}
# Re-running a pull request's failed GitHub Actions jobs, once, before a fix run is sent for them.
WRITE_ACTIONS = {"actions": "write"}
# A check run's GitHub Actions workflow run, from its details link.
ACTIONS_RUN = re.compile(r"/actions/runs/(\d+)")
# A check run that ends with one of these did not fail.
PASSING_CONCLUSIONS = {"success", "neutral", "skipped"}
PR_URL_PATTERN = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(\d+)$")
# A check run's summary, in a fix prompt, is cut to this many characters.
CHECK_SUMMARY_CHARS = 2000


class CodeHostError(RuntimeError):
    """A git command or code-host call failed. `retryable` is False when repeating it would fail the same way."""

    def __init__(self, message: str, retryable: bool = True, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        # The code host's HTTP status, when it answered one.
        self.status = status


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
    # The files the run created that carry a provenance header (run_service/provenance_headers.py).
    stamped: list[str] = field(default_factory=list)


@cache
def apps() -> GitHubApps:
    """One GitHubApps per process, so its installation tokens are cached across activities."""
    return GitHubApps()


# What the code host answers a push that changes a workflow file, from an App without the workflows permission,
# which the meta-harness's App is never given.
WORKFLOW_PUSH_REFUSED = "without `workflows` permission"

# Written into a clone's git directory once the clone, its checkout and its merge are done. The sandbox mounts
# that directory read-only, so only the host writes it.
CLONE_DONE = "mh-clone-done"


def is_local(repo: str) -> bool:
    return local_repos.is_local(repo)


def refuse_unlisted_local(repo: str) -> None:
    """Raises CodeHostError for a local repo the deployment does not vouch for (meta_harness.local_repos).
    Checked again at the clone and at the push, since a push runs the destination repo's hooks on the host."""
    if is_local(repo) and not local_repos.allowed(repo):
        raise CodeHostError(local_repos.refusal(repo), retryable=False)


def check_repo(repo: str) -> None:
    """Raises CodeHostError unless repo is owner/name with a GitHub App, or a local bare git repo the
    deployment allows."""
    if is_local(repo):
        refuse_unlisted_local(repo)
        if not (Path(repo) / "HEAD").is_file():
            raise CodeHostError(f"{repo} is not a bare git repo", retryable=False)
        return
    if not GITHUB_REPO_PATTERN.match(repo):
        raise CodeHostError(f"REPO {repo!r} must be owner/name on GitHub, or the path of a bare git repo", False)
    if acting.current():
        # The goal acts as its operator, whose own access decides; the repo needs no App.
        return
    try:
        apps().app_for(repo)
    except GitHubAppError as err:
        raise CodeHostError(str(err), retryable=False) from err


async def _github(repo: str, permissions: dict[str, str], method: str, path: str, **kwargs):
    """A REST call for the repo: as the operator the running work acts as (meta_harness.acting), else on the
    App's installation token with these permissions."""
    if who := acting.current():
        try:
            return await acting.call(who, method, path, **kwargs)
        except acting.ActingError as err:
            raise CodeHostError(str(err), retryable=err.retryable, status=err.status) from err
    try:
        return await apps().call(repo, permissions, method, path, **kwargs)
    except GitHubAppError as err:
        raise CodeHostError(str(err), retryable=err.retryable, status=err.status) from err


async def _token(repo: str, permissions: dict[str, str]) -> str:
    if who := acting.current():
        try:
            return await asyncio.to_thread(acting.token, who)
        except acting.ActingError as err:
            raise CodeHostError(str(err), retryable=err.retryable) from err
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


# "Paths: console/src/features/goals, src/meta_harness/planner" lines in an issue body.
PATHS_LINE = re.compile(r"^\s*paths:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def issue_paths(body: str) -> list[str]:
    """The paths a body's `Paths:` lines name, comma-separated, without backticks or leading and trailing
    slashes, in order and once each."""
    found: list[str] = []
    for line in PATHS_LINE.findall(body or ""):
        for item in line.split(","):
            path = item.strip().strip("`").strip().removeprefix("./").strip("/")
            if path and path not in found:
                found.append(path)
    return found


# The version rule outranks the paths rule: a review that read the version files as outside the task's paths had
# authors revert the bump the repository requires.
VERSION_RULE = {
    "review": (
        "The repository's own versioning rule, in its AGENTS.md, decides about its version files, whatever the "
        "task's paths say: a version change that rule asks for is never a finding, and one it forbids (as when "
        "it says the version is set at merge, not in a pull request), or a missing one it requires, is."
    ),
    "author": (
        "The repository's own versioning rule, in its AGENTS.md, decides about its version files, whatever the "
        "task's paths say: change them only as that rule says, and not at all when it says the version is set at "
        "merge, not in a pull request."
    ),
}


def paths_rule(description: str, kind: str) -> str:
    """The prompt's rule about the task's paths, from its description's `Paths:` lines, and the rule that the
    version files are always in scope. kind: task, review, fix."""
    paths = issue_paths(description)
    version = VERSION_RULE["review" if kind == "review" else "author"]
    if not paths:
        rule = (
            "The task names no paths, so the change should stay in the files the task is about."
            if kind == "review"
            else "Touch a file the task is not about only when the task cannot be done without it, and say so in your summary."
        )
        return f"{rule} {version}"
    listed = ", ".join(paths)
    if kind == "review":
        return (
            f"The task names its paths: {listed}. A changed file outside them, with no reason for it in the pull "
            f"request's summary, is a finding. {version}"
        )
    return (
        f"Keep to the task's paths: {listed}. Change a file outside them only when the task cannot be done "
        f"without it, and say why in your summary. {version}"
    )


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


async def clone(
    repo: str, workspace: Path, git_dir: Path, branch: str = "", merge_base: str = "", merge_message: str = ""
) -> str:
    """Clones repo into workspace, unless an earlier attempt did, and checks out branch, else the default
    branch. Returns the branch checked out. merge_message, with merge_base, is the message of the merge
    commit a clean merge makes, with its provenance; empty leaves git's own.

    The repository's git directory goes to git_dir, outside the workspace
    (`git clone --separate-git-dir`). The per-run sandbox mounts the
    workspace read-write and the git directory read-only, so pi can read
    history and diffs but cannot plant a hook, a `core.fsmonitor` command or
    any other git setting the host would run when it commits and pushes.
    """
    refuse_unlisted_local(repo)
    if git_dir.is_dir():
        if (git_dir / CLONE_DONE).is_file():
            # A retry of the run's activity: the clone an earlier attempt made, with whatever pi changed in it.
            return await _git(git_dir, workspace, "rev-parse", "--abbrev-ref", "HEAD")
        # That attempt stopped before the clone, its checkout or its merge was finished: a work tree with
        # files missing, or still on the default branch when the run is for a pull request's. Nothing in it
        # is pi's yet, so it is made again.
        await asyncio.to_thread(_remove_clone, workspace, git_dir)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    git_dir.parent.mkdir(parents=True, exist_ok=True)
    clone_args = ["clone", "--quiet", "--separate-git-dir", str(git_dir)]
    if is_local(repo):
        await _run("git", *clone_args, repo, str(workspace))
    else:
        env = git_auth_env(await _token(repo, READ_CODE))
        await _run("git", *clone_args, f"https://github.com/{repo}.git", str(workspace), env=env)
    if branch:
        # A review or fix run works on the pull request's branch; the clone has it as origin/<branch>.
        await _git(git_dir, workspace, "checkout", "--quiet", "-B", branch, f"origin/{branch}")
    if merge_base:
        # A conflict fix: merge the base in here, on the host. A clean merge commits; a conflict leaves its
        # markers and MERGE_HEAD, so the run resolves them and the publish's commit completes the merge.
        await _run(
            "git",
            f"--git-dir={git_dir}",
            f"--work-tree={workspace}",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            f"user.name={COMMIT_NAME}",
            "-c",
            f"user.email={COMMIT_EMAIL}",
            "merge",
            "--no-edit",
            *(["--message", merge_message] if merge_message else []),
            f"origin/{merge_base}",
            check=False,
            cwd=workspace,
        )
    current = await _git(git_dir, workspace, "rev-parse", "--abbrev-ref", "HEAD")
    (git_dir / CLONE_DONE).write_text("")
    return current


async def publish(
    *,
    repo: str,
    workspace: Path,
    git_dir: Path,
    branch: str,
    title: str,
    body: str | Callable[[list[str]], Awaitable[str]],
    commit_message: str,
    pr_record: Path,
    headers: Callable[[list[str]], Awaitable[list[str]]] | None = None,
) -> Published | None:
    """Commits whatever pi changed onto a new branch, pushes it, and opens the pull request.

    Returns None when pi changed nothing. A retried attempt finds the branch
    and the pull request the earlier attempt made, and makes neither again.
    body may be a function that writes the description from the files the
    branch changes: it is called only when a pull request is opened, so a
    description that costs something to write is not written for a push to
    a pull request already open.
    headers, when given, is called before the commit with the files the run
    created (`created_files`); it may add a provenance header to them, and
    returns the files that carry one, which the result's `stamped` lists.
    A retried attempt that finds the earlier attempt's commit, and nothing
    left to commit, hands it the files that commit created instead
    (`committed_files`), so `stamped` still lists them.
    Every git command names git_dir and the workspace outright, so the
    `.git` file pi could rewrite in the workspace decides nothing.
    """
    base = await _git(git_dir, workspace, "rev-parse", "--abbrev-ref", "origin/HEAD")
    base = base.removeprefix("origin/")
    current = await _git(git_dir, workspace, "rev-parse", "--abbrev-ref", "HEAD")
    if current != branch:
        await _git(git_dir, workspace, "checkout", "--quiet", "-B", branch)
    await _git(git_dir, workspace, "add", "--all")
    stamped: list[str] = []
    if headers is not None:
        created = await created_files(git_dir, workspace)
        if not created and not await _git(git_dir, workspace, "status", "--porcelain"):
            created = await committed_files(git_dir, workspace, commit_message)
        if created:
            stamped = await headers(created)
        if stamped:
            await _git(git_dir, workspace, "add", "--all")
    if await _git(git_dir, workspace, "status", "--porcelain"):
        await _git(
            git_dir,
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
    diff = await _git(git_dir, workspace, "diff", "--name-only", f"origin/{base}...HEAD")
    files = [f for f in diff.splitlines() if f]
    if not files:
        return None
    commit = await _git(git_dir, workspace, "rev-parse", "HEAD")
    refuse_unlisted_local(repo)
    env = None if is_local(repo) else git_auth_env(await _token(repo, WRITE_CODE))
    try:
        await _git(git_dir, workspace, "push", "--quiet", "--force-with-lease", "origin", f"{branch}:{branch}", env=env)
    except CodeHostError as err:
        if WORKFLOW_PUSH_REFUSED not in str(err):
            raise
        changed = ", ".join(f for f in files if f.startswith(".github/workflows/")) or "a workflow file"
        raise CodeHostError(
            f"the run changed {changed}, and Muxxy may not change a workflow file: the code host "
            "refuses that push from its App. A person makes this change. Cancel the task, and take the "
            "workflow file out of the issue's Paths if the rest stands without it",
            retryable=False,
        ) from err
    if is_local(repo):
        existing_record = pr_record.read_text(encoding="utf-8") if pr_record.exists() else ""
        # The record is this branch's pull request only when its head names this branch. A merge-check fix
        # publishes on a new branch while the record still holds the merged pull request, so that is a new one.
        # A record whose head commit the base already holds has merged too: a later run on that branch opens
        # a new one, with its own description and provenance.
        head = _HEAD_LINE.search(existing_record)
        created = head is None or head.group(1) != branch or await _merged_into(repo, base, head.group(2))
        if created:
            if not isinstance(body, str):
                body = await body(files)
            record = f"# {title}\n\nbase: {base}\nhead: {branch} at {commit}\n\n{body}\n"
        else:
            # A push to a pull request already open keeps its description, as GitHub does, and with it the
            # sections earlier runs added; only the head it names moves to the new commit.
            record = _HEAD_LINE.sub(lambda _: f"head: {branch} at {commit}", existing_record, count=1)
        pr_record.write_text(record, encoding="utf-8")
        return Published(branch, base, commit, files, f"file://{pr_record}", created, stamped=stamped)
    owner = repo.split("/", 1)[0]
    existing = await _github(
        repo, OPEN_PULL_REQUEST, "GET", f"/repos/{repo}/pulls", params={"head": f"{owner}:{branch}", "state": "open"}
    )
    if existing:
        return Published(branch, base, commit, files, existing[0]["html_url"], False, stamped=stamped)
    if not isinstance(body, str):
        body = await body(files)
    outbound.require(title, "a pull request's title", outbound.GITHUB, title=True)
    outbound.require(body, "a pull request's description", outbound.GITHUB)
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
    return Published(branch, base, commit, files, pull["html_url"], True, stamped=stamped)


async def created_files(git_dir: Path, workspace: Path) -> list[str]:
    """The files the run created, from what is staged: added against HEAD, with a file the run only moved left
    out, and, while a merge is in progress, the files the merged branch brought too."""
    staged = await _git(
        git_dir, workspace, "diff", "--cached", "--name-only", "--diff-filter=A", "--find-renames", "-z", "HEAD"
    )
    added = [name for name in staged.split("\0") if name]
    if added and (git_dir / "MERGE_HEAD").is_file():
        merged = await _git(git_dir, workspace, "ls-tree", "-r", "-z", "--name-only", "MERGE_HEAD")
        brought = set(merged.split("\0"))
        added = [name for name in added if name not in brought]
    return added


async def committed_files(git_dir: Path, workspace: Path, commit_message: str) -> list[str]:
    """The files an earlier attempt of this publish created, when HEAD is the commit it made (its author and
    message are this publish's); else none. Like `created_files`: added against the first parent, a moved file
    left out, and, for a merge, the files another parent brought too."""
    made = await _git(git_dir, workspace, "log", "-1", "--format=%an%n%B", "HEAD")
    author, _, message = made.partition("\n")
    if author != COMMIT_NAME or message.strip() != commit_message.strip():
        return []
    parents = (await _git(git_dir, workspace, "rev-list", "--parents", "-n", "1", "HEAD")).split()[1:]
    if not parents:
        return []
    changed = await _git(
        git_dir, workspace, "diff", "--name-only", "--diff-filter=A", "--find-renames", "-z", parents[0], "HEAD"
    )
    added = [name for name in changed.split("\0") if name]
    for other in parents[1:]:
        if not added:
            break
        merged = await _git(git_dir, workspace, "ls-tree", "-r", "-z", "--name-only", other)
        brought = set(merged.split("\0"))
        added = [name for name in added if name not in brought]
    return added


def pr_number(pr_url: str) -> tuple[str, int]:
    """The repo and number of a GitHub pull request URL."""
    match = PR_URL_PATTERN.match(pr_url)
    if not match:
        raise CodeHostError(f"{pr_url!r} is not a GitHub pull request URL", retryable=False)
    return match.group(1), int(match.group(2))


@dataclass
class CheckRun:
    name: str
    # queued, in_progress or completed; conclusion is set once completed.
    status: str
    conclusion: str | None
    summary: str
    # The GitHub Actions workflow run behind the check, when it is one: what a re-run of its failed jobs names.
    actions_run: int | None = None


# The app whose check suites say work is coming: a workflow run that is queued has a suite before it has a check
# run. Other apps get a suite on every push whether or not they ever report, so theirs say nothing.
ACTIONS_APP = "github-actions"


async def pending_suites(repo: str, sha: str) -> int:
    """How many GitHub Actions check suites on a commit have not completed. A workflow that is queued or
    waiting shows here before any of its jobs is a check run, so the check runs listed at one moment are not
    yet all the commit will have."""
    found = await _github(
        repo, READ_CHECKS, "GET", f"/repos/{repo}/commits/{sha}/check-suites", params={"per_page": 100}
    )
    return sum(
        1
        for suite in (found or {}).get("check_suites", [])
        if (suite.get("app") or {}).get("slug") == ACTIONS_APP and suite.get("status") != "completed"
    )


async def check_runs(repo: str, sha: str) -> list[CheckRun]:
    """Every check on a commit: its check runs, and its commit statuses as runs of their own."""
    runs = await _github(repo, READ_CHECKS, "GET", f"/repos/{repo}/commits/{sha}/check-runs", params={"per_page": 100})
    found = [
        CheckRun(
            name=run["name"],
            status=run["status"],
            conclusion=run.get("conclusion"),
            summary=_check_summary(run.get("output") or {}),
            actions_run=_actions_run(run.get("details_url") or run.get("html_url") or ""),
        )
        for run in runs.get("check_runs", [])
    ]
    combined = await _github(repo, READ_CHECKS, "GET", f"/repos/{repo}/commits/{sha}/status")
    for status in combined.get("statuses", []):
        state = status["state"]
        found.append(
            CheckRun(
                name=status["context"],
                status="in_progress" if state == "pending" else "completed",
                conclusion=None if state == "pending" else ("success" if state == "success" else "failure"),
                summary=status.get("description") or "",
            )
        )
    return found


def _actions_run(url: str) -> int | None:
    found = ACTIONS_RUN.search(url)
    return int(found[1]) if found else None


async def run_attempt(repo: str, run_id: int) -> int:
    """Which attempt a GitHub Actions workflow run is on: 1 until its jobs are re-run."""
    run = await _github(repo, READ_ACTIONS, "GET", f"/repos/{repo}/actions/runs/{run_id}")
    return int(run.get("run_attempt") or 1)


async def rerun_failed_jobs(repo: str, run_id: int) -> None:
    """Re-run a GitHub Actions workflow run's failed jobs, as Re-run failed jobs on GitHub does."""
    await _github(repo, WRITE_ACTIONS, "POST", f"/repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs")


def _seconds(started: str | None, completed: str | None) -> int | None:
    """How long a check ran, from the code host's two times; None while it has not finished."""
    if not started or not completed:
        return None
    try:
        took = datetime.fromisoformat(completed.replace("Z", "+00:00")) - datetime.fromisoformat(
            started.replace("Z", "+00:00")
        )
    except ValueError:
        return None
    return max(0, int(took.total_seconds()))


async def check_details(repo: str, ref: str) -> list[dict[str, Any]]:
    """Every check on a commit (or a branch's newest), for an operator to watch: its name, how it stands, when it
    started and ended, how long it ran, and where its log is. Commit statuses are included as checks of their
    own, with no times. Read only."""
    runs = await _github(repo, READ_CHECKS, "GET", f"/repos/{repo}/commits/{ref}/check-runs", params={"per_page": 100})
    found = [
        {
            "name": run["name"],
            "status": run["status"],
            "conclusion": run.get("conclusion"),
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
            "seconds": _seconds(run.get("started_at"), run.get("completed_at")),
            "url": run.get("html_url") or run.get("details_url"),
        }
        for run in (runs or {}).get("check_runs", [])
    ]
    combined = await _github(repo, READ_CHECKS, "GET", f"/repos/{repo}/commits/{ref}/status")
    for status in (combined or {}).get("statuses", []):
        pending = status["state"] == "pending"
        found.append(
            {
                "name": status["context"],
                "status": "in_progress" if pending else "completed",
                "conclusion": None if pending else ("success" if status["state"] == "success" else "failure"),
                "started_at": None,
                "completed_at": None,
                "seconds": None,
                "url": status.get("target_url"),
            }
        )
    return sorted(found, key=lambda check: check["name"].lower())


@dataclass
class ChecksState:
    # pending (a check has not completed), passed, failed, or none (the commit has no checks yet).
    state: str
    failed: list[str]


def checks_state(runs: list[CheckRun]) -> ChecksState:
    """How a commit's checks stand: pending until every one completes, then passed or failed."""
    if not runs:
        return ChecksState("none", [])
    if any(r.status != "completed" for r in runs):
        return ChecksState("pending", [])
    failed = [r.name for r in runs if r.conclusion not in PASSING_CONCLUSIONS]
    return ChecksState("failed" if failed else "passed", failed)


@dataclass
class PullRequest:
    repo: str
    number: int
    url: str
    # open, draft, merged or closed (closed without merging).
    state: str
    head_sha: str
    merge_sha: str


async def read_pull_request(repo: str, number: int) -> PullRequest:
    """A pull request's state and head commit. Reads only, with `pull_requests: read`."""
    pull = await _github(repo, READ_PULL_REQUEST, "GET", f"/repos/{repo}/pulls/{number}")
    if pull.get("merged") or pull.get("merged_at"):
        state = "merged"
    elif pull.get("state") == "closed":
        state = "closed"
    else:
        state = "draft" if pull.get("draft") else "open"
    return PullRequest(
        repo=repo,
        number=number,
        url=str(pull.get("html_url") or f"https://github.com/{repo}/pull/{number}"),
        state=state,
        head_sha=str((pull.get("head") or {}).get("sha") or ""),
        merge_sha=str(pull.get("merge_commit_sha") or "") if state == "merged" else "",
    )


async def branch_head(repo: str, branch: str) -> str:
    """The branch's newest commit."""
    commit = await _github(repo, READ_CODE, "GET", f"/repos/{repo}/commits/{branch}") or {}
    return str(commit.get("sha", ""))


async def base_head(repo: str) -> str:
    """The newest commit of the repo's default branch: the base a task's run starts from."""
    if is_local(repo):
        return await _run("git", "-C", repo, "rev-parse", "HEAD")
    return await branch_head(repo, "HEAD")


async def checkout(git_dir: Path, workspace: Path, commit: str) -> None:
    """Puts the clone's work tree at that commit, on no branch."""
    await _git(git_dir, workspace, "checkout", "--quiet", "--detach", commit)


async def pull_request(repo: str, number: int) -> dict:
    """The pull request as the code host returns it: its state, whether it merged, and its head and base."""
    return await _github(repo, READ_PULL_REQUEST, "GET", f"/repos/{repo}/pulls/{number}") or {}


def _check_summary(output: dict) -> str:
    text = "\n\n".join(part for part in (output.get("title"), output.get("summary"), output.get("text")) if part)
    return text[:CHECK_SUMMARY_CHARS]


async def post_review(
    repo: str, number: int, head_sha: str, body: str, comments: list[dict], provenance: Provenance | None = None
) -> str:
    """Posts a review on the pull request at head_sha, with inline comments, and returns its URL.

    It is a comment review: GitHub refuses an approval or a change request
    from the pull request's own author, and the meta-harness's GitHub App
    opened it. A comment GitHub cannot place on the diff (422) moves into the
    review's body, so the review is never lost to a finding on an unchanged line.
    With provenance, the review's body and each inline comment end with its provenance line, as GitHub shows
    each inline comment on its own. A comment moved into the body goes without its own line, as the body ends
    with one.
    """

    outbound.require(body, "a review's body", outbound.GITHUB)
    for placed_comment in comments:
        outbound.require(placed_comment["body"], "a review's inline comment", outbound.GITHUB)

    def stamped(text: str) -> str:
        return stamp(text, provenance) if provenance else text

    inline = [{**c, "body": stamped(c["body"])} for c in comments]
    payload = {"commit_id": head_sha, "event": "COMMENT", "body": stamped(body), "comments": inline}
    try:
        review = await _github(repo, REVIEW, "POST", f"/repos/{repo}/pulls/{number}/reviews", json=payload)
    except CodeHostError as err:
        if not comments or err.status != 422:
            raise
        placed = "\n".join(f"- `{c['path']}` line {c['line']}: {c['body']}" for c in comments)
        payload = {"commit_id": head_sha, "event": "COMMENT", "body": stamped(f"{body}\n\n{placed}"), "comments": []}
        review = await _github(repo, REVIEW, "POST", f"/repos/{repo}/pulls/{number}/reviews", json=payload)
    return str(review.get("html_url", ""))


async def pull_request_files(repo: str, number: int) -> list[dict]:
    """Each file the pull request changes: its path, status, added and removed line counts, and patch."""
    files: list[dict] = []
    page = 1
    while True:
        batch = await _github(
            repo,
            READ_PULL_REQUEST,
            "GET",
            f"/repos/{repo}/pulls/{number}/files",
            params={"per_page": 100, "page": page},
        )
        files += [
            {
                "path": f["filename"],
                # A renamed file's old path: a change that moves a file out of a path still changed that path.
                "previous_path": f.get("previous_filename"),
                "status": f.get("status", "modified"),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "patch": f.get("patch"),
            }
            for f in batch or []
        ]
        if len(batch or []) < 100:
            return files
        page += 1


async def comment_on_lines(
    repo: str,
    number: int,
    head_sha: str,
    path: str,
    line: int,
    body: str,
    start_line: int | None = None,
    provenance: Provenance | None = None,
) -> str:
    """Posts a review comment on lines of the pull request's head commit (the new side), and returns its URL.
    With provenance, the comment ends with its provenance line."""
    outbound.require(body, "a comment on a pull request's lines", outbound.GITHUB)
    body = stamp(body, provenance) if provenance else body
    payload: dict = {"commit_id": head_sha, "path": path, "line": line, "side": "RIGHT", "body": body}
    if start_line is not None and start_line < line:
        payload |= {"start_line": start_line, "start_side": "RIGHT"}
    comment = await _github(repo, REVIEW, "POST", f"/repos/{repo}/pulls/{number}/comments", json=payload)
    return str(comment.get("html_url", ""))


async def close_pull_request(
    repo: str, number: int, body: str, branch: str, provenance: Provenance | None = None
) -> str:
    """Closes an open pull request with a comment saying why, and deletes its branch. Returns what was done.
    One already closed gets no second comment, and a branch already gone is no error, so a repeat is harmless.
    A merged pull request is left alone: its work is on the base."""
    outbound.require(body, "a comment on a pull request", outbound.GITHUB)
    pull = await _github(repo, READ_PULL_REQUEST, "GET", f"/repos/{repo}/pulls/{number}") or {}
    if pull.get("merged") or pull.get("merged_at"):
        return "the pull request had merged, so it was left as it is"
    did = []
    if pull.get("state") == "open":
        await comment(repo, number, body, provenance=provenance)
        await _github(repo, REVIEW, "PATCH", f"/repos/{repo}/pulls/{number}", json={"state": "closed"})
        did.append("closed the pull request")
    try:
        await _github(repo, WRITE_CODE, "DELETE", f"/repos/{repo}/git/refs/heads/{branch}")
        did.append(f"deleted the branch {branch}")
    except CodeHostError as err:
        # 422 or 404: the branch is already gone.
        if err.status not in (404, 422):
            raise
    return ", and ".join(did) or "the pull request was closed and its branch gone already"


async def comment(repo: str, number: int, body: str, provenance: Provenance | None = None) -> str:
    """Posts a comment on the pull request's conversation, and returns its URL. With provenance, the comment
    ends with its provenance line."""
    outbound.require(body, "a comment on a pull request", outbound.GITHUB)
    body = stamp(body, provenance) if provenance else body
    posted = await _github(repo, REVIEW, "POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body})
    return str((posted or {}).get("html_url", ""))


MERGE = {"contents": "write", "pull_requests": "write"}


async def newest_containing(repo: str, base: str, sha: str) -> str | None:
    """The newest commit on base, when it contains sha; None when base does not hold sha."""
    head = await _github(repo, READ_CODE, "GET", f"/repos/{repo}/commits/{base}") or {}
    newest = str(head.get("sha", ""))
    if not newest:
        return None
    if newest == sha:
        return newest
    compare = await _github(repo, READ_CODE, "GET", f"/repos/{repo}/compare/{sha}...{newest}") or {}
    return newest if compare.get("status") in ("ahead", "identical") else None


async def merge_pull_request(
    repo: str,
    number: int,
    head_sha: str,
    base: str | None = None,
    kind: str = "",
    provenance: Provenance | None = None,
) -> tuple[str, str, str, str]:
    """Squash-merges the pull request if its head is still head_sha. Returns (state, merge sha, head, detail).

    state is merged, head_changed (a commit landed after head_sha, or the base was brought in; head is the
    new one), conflict (the branch conflicts with its base), or blocked (GitHub refused for another reason,
    such as branch protection or a required human review).

    kind (fix, feature or none) goes into the merge commit's message as a `Version:` line, and provenance as
    the trailers after it.

    With base, a branch that lacks the base's newest commits is not merged: the base is merged into it, in a
    merge commit that carries provenance's trailers, and the new head comes back as head_changed, so its checks and review run again before it merges. So no
    pull request merges against a base it was not checked on, though other tasks' pull requests merged
    while it was reviewed.
    """
    # A merge whose answer was lost has still merged. Asked again, it must say so: bringing the base into a
    # merged branch, or calling the pull request blocked, would leave its task open with its work on the base.
    # Both reads of the pull request below look for that: before the base is brought in, and after a refusal.
    if base:
        brought = await _bring_base_in(repo, number, base, head_sha, provenance)
        if brought is not None:
            return brought
    merge = {"sha": head_sha, "merge_method": "squash"}
    # The merge commit says what kind of change it is, so a repo that works its version out from its
    # history (as this one does) counts it right: a feature, a fix, or a change that ships nothing.
    version = [f"Version: {kind}"] if kind else []
    if provenance:
        merge["commit_message"] = commit_message("", provenance, *version)
    elif version:
        merge["commit_message"] = version[0]
    try:
        merged = await _github(repo, MERGE, "PUT", f"/repos/{repo}/pulls/{number}/merge", json=merge)
        return "merged", str(merged.get("sha", "")), head_sha, ""
    except CodeHostError as err:
        if err.status not in (405, 409, 422):
            raise
        refused = str(err)
    pull = await _github(repo, {"pull_requests": "read"}, "GET", f"/repos/{repo}/pulls/{number}")
    done = _merged(pull, head_sha)
    if done is not None:
        return done
    head = str(pull.get("head", {}).get("sha", ""))
    if head and head != head_sha:
        return "head_changed", "", head, refused
    if pull.get("mergeable_state") == "dirty" or pull.get("mergeable") is False:
        return "conflict", "", head_sha, refused
    return "blocked", "", head_sha, f"{refused} (mergeable_state {pull.get('mergeable_state')})"


def _merged(pull: dict | None, head_sha: str) -> tuple[str, str, str, str] | None:
    """The answer for a pull request that has already merged, or None when it has not."""
    if not (pull or {}).get("merged"):
        return None
    return "merged", str(pull.get("merge_commit_sha") or ""), head_sha, ""


async def bring_base_in(
    repo: str, number: int, base: str, head_sha: str, provenance: Provenance | None = None
) -> tuple[str, str, str, str]:
    """Brings the base's newest commits into the pull request's branch, before a review reads it. Returns
    (state, "", head, detail): current when the branch already holds them, head_changed with the new head, or
    conflict when the base does not merge in cleanly."""
    brought = await _bring_base_in(repo, number, base, head_sha, provenance)
    # A pull request that merged meanwhile has nothing to bring in: its caller's next read of it says so.
    return brought if brought is not None and brought[0] != "merged" else ("current", "", head_sha, "")


async def _bring_base_in(
    repo: str, number: int, base: str, head_sha: str, provenance: Provenance | None = None
) -> tuple[str, str, str, str] | None:
    """None when head_sha holds the base's newest commit. Otherwise merges the base into the branch and returns
    head_changed with the new head, or conflict when the base does not merge in cleanly. A pull request that has
    already merged comes back as merged, and nothing is brought into its branch.

    The merge goes through GitHub's merges endpoint, not update-branch, so its commit message is ours and
    carries provenance's trailers. That endpoint takes no expected head, so the branch's head is read first;
    a commit pushed between that read and the merge only ends up under the merge commit, whose new head
    comes back as head_changed and is checked and reviewed again like any other."""
    compare = await _github(repo, READ_CODE, "GET", f"/repos/{repo}/compare/{base}...{head_sha}") or {}
    behind = int(compare.get("behind_by") or 0)
    if behind == 0:
        return None
    pull = await _github(repo, READ_PULL_REQUEST, "GET", f"/repos/{repo}/pulls/{number}") or {}
    done = _merged(pull, head_sha)
    if done is not None:
        return done
    head = str(pull.get("head", {}).get("sha", ""))
    if head and head != head_sha:
        return "head_changed", "", head, f"{head[:12]} landed after {head_sha[:12]}"
    branch = str(pull.get("head", {}).get("ref", ""))
    if not branch:
        raise CodeHostError(f"pull request {number} names no head branch to bring {base} into")
    subject = f"Merge {base} into {branch}"
    message = commit_message(subject, provenance) if provenance else subject
    try:
        merged = await _github(
            repo, MERGE, "POST", f"/repos/{repo}/merges", json={"base": branch, "head": base, "commit_message": message}
        )
    except CodeHostError as err:
        if err.status != 409:
            raise
        return "conflict", "", head_sha, f"{base} is {behind} commits ahead and does not merge in cleanly: {err}"
    new_head = str((merged or {}).get("sha", ""))
    if not new_head:
        # 204: GitHub found nothing to merge, so the branch already holds the base.
        return None
    return "head_changed", "", new_head, f"brought in {behind} new commits from {base}"


# GitHub's compare lists at most this many files; a diff that may be cut is never called the same.
COMPARE_FILES = 300


async def pull_request_diff(repo: str, base: str, head_sha: str) -> dict[str, str] | None:
    """The pull request's own change at head_sha: each file it changes against where it left base, with its
    patch. None when it cannot be read whole: too many files, or a file with no patch (binary, or too large)."""
    compare = await _github(repo, READ_CODE, "GET", f"/repos/{repo}/compare/{base}...{head_sha}") or {}
    files = compare.get("files")
    if files is None or len(files) >= COMPARE_FILES:
        return None
    diff: dict[str, str] = {}
    for changed in files:
        if changed.get("patch") is None:
            return None
        diff[str(changed["filename"])] = f"{changed.get('status')}\n{changed['patch']}"
    return diff


async def same_diff(repo: str, base: str, before: str, after: str) -> bool:
    """Whether the pull request changes the same lines at after as at before, as it does when the base was
    brought in and touched none of its files. So a review of before still holds for after."""
    old = await pull_request_diff(repo, base, before)
    if old is None:
        return False
    return old == await pull_request_diff(repo, base, after)


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


def hunk_lines(patch: str) -> list[tuple[int, int]]:
    """The new-side line ranges a unified diff's hunks cover, first and last line, inclusive. A hunk that only
    deletes covers the line it deleted next to, so a finding there still counts as on changed code."""
    ranges = []
    for match in HUNK.finditer(patch):
        start, count = int(match.group(1)), int(match.group(2) if match.group(2) is not None else 1)
        ranges.append((start, start + max(count, 1) - 1))
    return ranges


async def changed_lines(repo: str, since: str, head_sha: str) -> dict[str, list[tuple[int, int]]] | None:
    """Each file changed between two commits of a branch, with the line ranges at head_sha that changed. None
    when the change cannot be read whole (too many files, or a file with no patch), so nothing is taken as
    unchanged that may have changed."""
    if is_local(repo):
        diff = await _run("git", f"--git-dir={repo}", "diff", "--unified=0", "--no-color", since, head_sha)
        found: dict[str, list[tuple[int, int]]] = {}
        for part in re.split(r"^diff --git ", diff, flags=re.MULTILINE)[1:]:
            name = re.search(r"^\+\+\+ (?:b/)?(.+)$", part, re.MULTILINE) or re.search(
                r"^--- a/(.+)$", part, re.MULTILINE
            )
            if name:
                found[name.group(1).strip()] = hunk_lines(part)
        return found
    compare = await _github(repo, READ_CODE, "GET", f"/repos/{repo}/compare/{since}...{head_sha}") or {}
    files = compare.get("files")
    if files is None or len(files) >= COMPARE_FILES:
        return None
    found = {}
    for changed in files:
        if changed.get("patch") is None:
            return None
        found[str(changed["filename"])] = hunk_lines(changed["patch"])
    return found


_HEAD_LINE = re.compile(r"^head: (\S+)(?: at (\S*))?$", re.MULTILINE)


async def _merged_into(repo: str, base: str, commit: str | None) -> bool:
    """Whether a local bare repo's base holds commit, so the pull request at that head has merged. A commit
    the repo does not know has not merged."""
    if not commit:
        return False
    found = await _run("git", f"--git-dir={repo}", "merge-base", base, commit, check=False)
    return bool(found) and found == await _run("git", f"--git-dir={repo}", "rev-parse", commit, check=False)


async def description(repo: str, pr_url: str, pr_record: Path) -> str:
    """The pull request's description as the code host holds it now. A local repo's pull request is its record
    file. Read only."""
    if is_local(repo):
        return pr_record.read_text(encoding="utf-8") if pr_record.exists() else ""
    owner_repo, number = pr_number(pr_url)
    pull = await _github(owner_repo, READ_PULL_REQUEST, "GET", f"/repos/{owner_repo}/pulls/{number}") or {}
    return str(pull.get("body") or "").replace("\r\n", "\n")


async def add_to_description(repo: str, pr_url: str, heading: str, text: str, pr_record: Path) -> str:
    """Adds a section to the pull request's description, once: a retry finds its heading there and adds
    nothing. Returns the description as it then stands, so a caller can read back the section it holds, which
    is an earlier attempt's when one added it. A local repo's pull request is its record file."""
    outbound.require(text, "a section added to a pull request's description", outbound.GITHUB)
    section = f"\n\n## {heading}\n\n{text.strip()}\n"
    if is_local(repo):
        current = pr_record.read_text(encoding="utf-8") if pr_record.exists() else ""
        if f"## {heading}\n" not in current:
            current = current.rstrip() + section
            pr_record.write_text(current, encoding="utf-8")
        return current
    owner_repo, number = pr_number(pr_url)
    pull = await _github(owner_repo, READ_PULL_REQUEST, "GET", f"/repos/{owner_repo}/pulls/{number}") or {}
    current = str(pull.get("body") or "")
    if f"## {heading}\n" in current:
        return current.replace("\r\n", "\n")
    current = current.rstrip() + section
    await _github(
        owner_repo,
        OPEN_PULL_REQUEST,
        "PATCH",
        f"/repos/{owner_repo}/pulls/{number}",
        json={"body": current},
    )
    return current.replace("\r\n", "\n")


async def merge_local(repo: str, base: str, head_sha: str) -> tuple[str, str, str, str]:
    """A local bare repo's merge: fast-forward its base to head_sha, or a conflict when the base has moved on."""
    tip = await _run("git", f"--git-dir={repo}", "rev-parse", base)
    if await _run("git", f"--git-dir={repo}", "merge-base", base, head_sha) != tip:
        return "conflict", "", head_sha, f"{base} has moved on since the branch left it"
    await _run("git", f"--git-dir={repo}", "update-ref", f"refs/heads/{base}", head_sha, tip)
    return "merged", head_sha, head_sha, ""


async def _git(git_dir: Path, workspace: Path, *args: str, env: dict[str, str] | None = None) -> str:
    """One git command on the run's repository, with its git directory and work tree named outright.

    Hooks are turned off too. The git directory is outside the sandbox
    mount, so pi cannot write one there; this keeps the host from running a
    hook under any other path into the directory.

    A repository inside the workspace is refused first: git reads a nested
    repository's own configuration when it looks at one (status and diff
    start a git there), so what pi wrote into it would run on the host.
    Submodules are ignored and the file monitor is off as well, should one
    get past the check.
    """
    await asyncio.to_thread(refuse_nested_repos, workspace)
    if args and args[0] in ("status", "diff"):
        args = (args[0], "--ignore-submodules=all", *args[1:])
    return await _run(
        "git",
        f"--git-dir={git_dir}",
        f"--work-tree={workspace}",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "submodule.recurse=false",
        *args,
        env=env,
    )


def refuse_nested_repos(workspace: Path) -> None:
    """Raises when the workspace holds a git repository below its top: any `.git` file, directory or link
    other than the workspace's own pointer file."""
    for folder, dirs, files in os.walk(workspace, followlinks=False):
        if Path(folder) != workspace and ".git" in (*dirs, *files):
            where = Path(folder).relative_to(workspace)
            raise CodeHostError(
                f"the run left a git repository inside its workspace, at {where}. The control plane runs no git "
                "command over a workspace that holds one, because git would run what that repository's "
                "configuration names. Retry the task; if the task needs a second repository, clone it outside "
                "the workspace.",
                retryable=False,
            )


def _remove_clone(workspace: Path, git_dir: Path) -> None:
    """Removes an unfinished clone's two directories, so the clone can be made again."""
    for directory in (workspace, git_dir):
        shutil.rmtree(directory, ignore_errors=True)


async def _run(*args: str, check: bool = True, env: dict[str, str] | None = None, cwd: Path | None = None) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **env} if env else None,
            cwd=cwd,
        )
    except FileNotFoundError as err:
        raise CodeHostError(f"the {args[0]} command is not installed", retryable=False) from err
    out, err = await process.communicate()
    if check and process.returncode != 0:
        raise CodeHostError(f"{' '.join(args[:3])} failed: {err.decode(errors='replace').strip()}")
    return out.decode(errors="replace").strip()


# A run's diff is cut here for the compare view: a page shows the start of a large one.
DIFF_CHARS = 200_000


async def workspace_diff(ref: str, run_id: str) -> dict:
    """A run's changes in its workspace against the commit it cloned, new files included: per file counts and the
    patch. Nothing is committed or pushed."""
    from meta_harness.run_service import sandbox

    workspace, git_dir = sandbox.workspace_for(ref, run_id), sandbox.git_dir_for(ref, run_id)
    if not workspace.is_dir() or not git_dir.is_dir():
        return {"files": [], "patch": "", "missing": True}
    await _git(git_dir, workspace, "add", "--all", "--intent-to-add")
    numstat = await _git(git_dir, workspace, "diff", "HEAD", "--numstat")
    files = []
    for line in numstat.splitlines():
        added, removed, path = (line.split("\t", 2) + ["", "", ""])[:3]
        files.append(
            {
                "path": path,
                "additions": int(added) if added.isdigit() else 0,
                "deletions": int(removed) if removed.isdigit() else 0,
            }
        )
    patch = await _git(git_dir, workspace, "diff", "HEAD")
    return {"files": files, "patch": patch[:DIFF_CHARS], "missing": False}
