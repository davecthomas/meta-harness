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
and pushes. A local repo makes the whole path testable with no
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
READ_CHECKS = {"checks": "read", "statuses": "read"}
REVIEW = {"pull_requests": "write"}
READ_PULL_REQUEST = {"pull_requests": "read"}
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
        raise CodeHostError(str(err), retryable=err.retryable, status=err.status) from err


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


async def clone(repo: str, workspace: Path, git_dir: Path, branch: str = "", merge_base: str = "") -> str:
    """Clones repo into workspace, unless an earlier attempt did, and checks out branch, else the default
    branch. Returns the branch checked out.

    The repository's git directory goes to git_dir, outside the workspace
    (`git clone --separate-git-dir`). The per-run sandbox mounts the
    workspace read-write and the git directory read-only, so pi can read
    history and diffs but cannot plant a hook, a `core.fsmonitor` command or
    any other git setting the host would run when it commits and pushes.
    """
    if git_dir.is_dir():
        return await _git(git_dir, workspace, "rev-parse", "--abbrev-ref", "HEAD")
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
            f"origin/{merge_base}",
            check=False,
            cwd=workspace,
        )
    return await _git(git_dir, workspace, "rev-parse", "--abbrev-ref", "HEAD")


async def publish(
    *,
    repo: str,
    workspace: Path,
    git_dir: Path,
    branch: str,
    title: str,
    body: str,
    commit_message: str,
    pr_record: Path,
) -> Published | None:
    """Commits whatever pi changed onto a new branch, pushes it, and opens the pull request.

    Returns None when pi changed nothing. A retried attempt finds the branch
    and the pull request the earlier attempt made, and makes neither again.
    Every git command names git_dir and the workspace outright, so the
    `.git` file pi could rewrite in the workspace decides nothing.
    """
    base = await _git(git_dir, workspace, "rev-parse", "--abbrev-ref", "origin/HEAD")
    base = base.removeprefix("origin/")
    current = await _git(git_dir, workspace, "rev-parse", "--abbrev-ref", "HEAD")
    if current != branch:
        await _git(git_dir, workspace, "checkout", "--quiet", "-B", branch)
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
    env = None if is_local(repo) else git_auth_env(await _token(repo, WRITE_CODE))
    await _git(git_dir, workspace, "push", "--quiet", "--force-with-lease", "origin", f"{branch}:{branch}", env=env)
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


async def check_runs(repo: str, sha: str) -> list[CheckRun]:
    """Every check on a commit: its check runs, and its commit statuses as runs of their own."""
    runs = await _github(repo, READ_CHECKS, "GET", f"/repos/{repo}/commits/{sha}/check-runs", params={"per_page": 100})
    found = [
        CheckRun(
            name=run["name"],
            status=run["status"],
            conclusion=run.get("conclusion"),
            summary=_check_summary(run.get("output") or {}),
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


def _check_summary(output: dict) -> str:
    text = "\n\n".join(part for part in (output.get("title"), output.get("summary"), output.get("text")) if part)
    return text[:CHECK_SUMMARY_CHARS]


async def post_review(repo: str, number: int, head_sha: str, body: str, comments: list[dict]) -> str:
    """Posts a review on the pull request at head_sha, with inline comments, and returns its URL.

    It is a comment review: GitHub refuses an approval or a change request
    from the pull request's own author, and the meta-harness's GitHub App
    opened it. A comment GitHub cannot place on the diff (422) moves into the
    review's body, so the review is never lost to a finding on an unchanged line.
    """
    payload = {"commit_id": head_sha, "event": "COMMENT", "body": body, "comments": comments}
    try:
        review = await _github(repo, REVIEW, "POST", f"/repos/{repo}/pulls/{number}/reviews", json=payload)
    except CodeHostError as err:
        if not comments or err.status != 422:
            raise
        placed = "\n".join(f"- `{c['path']}` line {c['line']}: {c['body']}" for c in comments)
        payload = {"commit_id": head_sha, "event": "COMMENT", "body": f"{body}\n\n{placed}", "comments": []}
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
    repo: str, number: int, head_sha: str, path: str, line: int, body: str, start_line: int | None = None
) -> str:
    """Posts a review comment on lines of the pull request's head commit (the new side), and returns its URL."""
    payload: dict = {"commit_id": head_sha, "path": path, "line": line, "side": "RIGHT", "body": body}
    if start_line is not None and start_line < line:
        payload |= {"start_line": start_line, "start_side": "RIGHT"}
    comment = await _github(repo, REVIEW, "POST", f"/repos/{repo}/pulls/{number}/comments", json=payload)
    return str(comment.get("html_url", ""))


MERGE = {"contents": "write", "pull_requests": "write"}


async def merge_pull_request(repo: str, number: int, head_sha: str) -> tuple[str, str, str, str]:
    """Squash-merges the pull request if its head is still head_sha. Returns (state, merge sha, head, detail).

    state is merged, head_changed (a commit landed after head_sha; head is the new one), conflict (the
    branch conflicts with its base), or blocked (GitHub refused for another reason, such as branch
    protection or a required human review).
    """
    try:
        merged = await _github(
            repo, MERGE, "PUT", f"/repos/{repo}/pulls/{number}/merge", json={"sha": head_sha, "merge_method": "squash"}
        )
        return "merged", str(merged.get("sha", "")), head_sha, ""
    except CodeHostError as err:
        if err.status not in (405, 409, 422):
            raise
        refused = str(err)
    pull = await _github(repo, {"pull_requests": "read"}, "GET", f"/repos/{repo}/pulls/{number}")
    head = str(pull.get("head", {}).get("sha", ""))
    if head and head != head_sha:
        return "head_changed", "", head, refused
    if pull.get("mergeable_state") == "dirty" or pull.get("mergeable") is False:
        return "conflict", "", head_sha, refused
    return "blocked", "", head_sha, f"{refused} (mergeable_state {pull.get('mergeable_state')})"


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
    """
    return await _run(
        "git",
        f"--git-dir={git_dir}",
        f"--work-tree={workspace}",
        "-c",
        "core.hooksPath=/dev/null",
        *args,
        env=env,
    )


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
