"""The goal loop's intake: turn a goal's labeled issues into tasks, and release tasks whose dependencies closed.

For each active goal, a sweep lists the open issues with the goal's label in
each of its repos. An issue the planner has not seen becomes a task through
the Task API, with `external_ref` `github:<owner>/<repo>#<number>`, the
issue's text, its `## Acceptance checks` (else the repo registry's default
checks), and the open issues it depends on. The task service starts a task
with open dependencies in `blocked`. A sweep also records issues that closed,
and moves a blocked task to `queued` once every issue it depends on is
closed. An issue already in another goal stays there.

A sweep also watches the pull request of each of the goal's tasks that ended
`ready` or `review_unresolved`, which no RunTask workflow watches any more. It
reads the pull request and its head commit's checks from GitHub, records each
change as an event (`pull_request_checks` once a new head commit's checks
settle, `pull_request_merged`, `pull_request_closed`) with the event id the
workflow uses for the same fact, so the fact is recorded once whoever sees it,
and reports a merge to the task service as `closed` and a close without
merging as `canceled`.

Last, a sweep checks whether the goal is met: it has held at least one issue,
every issue it holds is closed, its label has no open issue it could take in,
and none of its tasks is queued, blocked, running or waiting on an operator.
A met goal moves to `met` and the planner emits `goal_met`. Only an `active`
goal is checked, so a paused goal, and one saved without starting, is left
alone.

The planner runs a sweep on every active goal every MH_PLANNER_SWEEP_SECONDS,
and right after a goal first starts: when it is created, or on its first
resume when it was saved without starting. Webhooks make it immediate later.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol

from meta_harness import db
from meta_harness.events.client import stable_event_id
from meta_harness.github_app import GitHubAppError
from meta_harness.planner import repos, store
from meta_harness.planner.tracker import Issue, Tracker
from meta_harness.run_service import code_host
from meta_harness.run_service.code_host import CheckRun, CodeHostError, PullRequest
from meta_harness.tasks.client import TaskApiError, TaskClient

TASK_TYPES = {"bug", "feature", "refactor", "dependency", "test", "security", "docs"}
# A goal's task in one of these still has work to do, or waits for it: the goal is not met.
UNFINISHED_TASK_STATUSES = ["queued", "blocked", "running", "taken_over", "ready", "review_unresolved"]
# A task in one of these left its pull request open with no workflow watching it: the sweep watches it.
PR_WATCHED_STATUSES = ["ready", "review_unresolved"]
# The most tasks one sweep watches per goal: the Task API's page limit.
PR_WATCH_LIMIT = 1000


class Events(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any] | None = None, **fields: Any) -> str: ...


class CodeHost(Protocol):
    """The pull request reads a sweep makes. The code_host module is one."""

    async def read_pull_request(self, repo: str, number: int) -> PullRequest: ...

    async def check_runs(self, repo: str, sha: str) -> list[CheckRun]: ...


@dataclass
class SweepResult:
    goal_id: str
    joined: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    unblocked: list[str] = field(default_factory=list)
    # Task ids whose pull request the sweep found merged, or closed without merging.
    merged: list[str] = field(default_factory=list)
    canceled: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    met: bool = False


def task_type_for(issue: Issue) -> str:
    for label in issue.labels:
        kind = label.removeprefix("type:")
        if label.startswith("type:") and kind in TASK_TYPES:
            return kind
    return "bug"


async def sweep_goal(
    goal: dict, tracker: Tracker, tasks: TaskClient, events: Events, host: CodeHost | None = None
) -> SweepResult:
    result = SweepResult(goal["id"])
    seen: set[str] = set()
    for repo in goal["repos"]:
        try:
            issues = await tracker.list_open(repo, goal["label"])
        except GitHubAppError as err:
            result.errors.append(f"{repo}: {err}")
            continue
        for issue in issues:
            seen.add(issue.ref)
            await _take_in(goal, issue, tracker, tasks, events, result)
    await _watch_pull_requests(goal, tasks, events, host or code_host, result)
    await _record_closed(goal, seen, tracker, events, result)
    await _release_unblocked(goal, tracker, tasks, events, result)
    if not result.errors:
        await _complete_if_met(goal, tracker, tasks, events, result)
    return result


def issues_done(links: list[dict]) -> bool:
    """Whether a goal's issues are all closed. A goal that never held an issue waits for its first one."""
    return bool(links) and all(link["state"] == "closed" for link in links)


async def _complete_if_met(goal: dict, tracker: Tracker, tasks: TaskClient, events: Events, result: SweepResult):
    """Move an active goal to met once every issue it holds is closed and none of its tasks has work left."""
    if goal["status"] != "active" or not issues_done(await asyncio.to_thread(_links, goal["id"])):
        return
    try:
        unfinished = await asyncio.to_thread(tasks.list, UNFINISHED_TASK_STATUSES, goal["id"], 1)
    except TaskApiError as err:
        result.errors.append(f"{goal['id']}: {err}")
        return
    if unfinished:
        return
    # The label may have picked up an issue since the sweep listed it: look once more before closing the goal.
    for repo in goal["repos"]:
        try:
            issues = await tracker.list_open(repo, goal["label"])
        except GitHubAppError as err:
            result.errors.append(f"{repo}: {err}")
            return
        for issue in issues:
            existing = await asyncio.to_thread(_link, issue.ref)
            if existing is None or existing["goal_id"] == goal["id"]:
                return
    met, changed = await asyncio.to_thread(_complete, goal["id"])
    if not changed:
        return
    events.emit(
        "goal_changed",
        {"goal_id": goal["id"], "changes": {"status": {"from": changed["status"], "to": met["status"]}}},
        actor="planner",
        tags={"goal_id": goal["id"]},
    )
    events.emit(
        "goal_met",
        {"goal_id": goal["id"], "reason": "every issue it holds is closed and none of its tasks has work left"},
        actor="planner",
        tags={"goal_id": goal["id"]},
        event_id=stable_event_id("goal_met", goal["id"], met["closed_at"]),
    )
    goal["status"] = met["status"]
    result.met = True


async def _take_in(goal: dict, issue: Issue, tracker: Tracker, tasks: TaskClient, events: Events, result: SweepResult):
    existing = await asyncio.to_thread(_link, issue.ref)
    if existing and (existing["goal_id"] != goal["id"] or existing["task_id"] or existing["refused_reason"]):
        return
    registered = repos.get(issue.repo)
    checks = issue.checks or (registered.default_checks if registered else [])
    if not checks:
        reason = "the issue has no ## Acceptance checks and the repo registry names no default checks"
        await asyncio.to_thread(_save, issue.ref, goal["id"], issue.repo, "open", issue.depends_on, None, reason)
        events.emit(
            "issue_refused",
            {"ref": issue.ref, "reason": reason},
            actor="planner",
            tags={"goal_id": goal["id"]},
            event_id=stable_event_id("issue_refused", issue.ref),
        )
        result.refused.append(issue.ref)
        return
    open_deps = [ref for ref in issue.depends_on if await _is_open(ref, tracker)]
    try:
        task = await asyncio.to_thread(
            tasks.create,
            external_ref=issue.ref,
            repo=issue.repo,
            task_type=task_type_for(issue),
            priority=goal["priority"],
            title=issue.title[:300],
            description=issue.body.strip() or issue.title,
            acceptance_checks=checks,
            goal_id=goal["id"],
            depends_on=open_deps or None,
        )
    except TaskApiError as err:
        result.errors.append(f"{issue.ref}: {err}")
        return
    await asyncio.to_thread(_save, issue.ref, goal["id"], issue.repo, "open", open_deps, task["id"], None)
    events.emit(
        "issue_joined",
        {"ref": issue.ref, "goal_id": goal["id"], "depends_on": open_deps, "status": task["status"]},
        task_id=task["id"],
        actor="planner",
        event_id=stable_event_id("issue_joined", issue.ref, goal["id"]),
    )
    result.joined.append(issue.ref)


async def _record_closed(goal: dict, seen: set[str], tracker: Tracker, events: Events, result: SweepResult) -> None:
    for link in await asyncio.to_thread(_links, goal["id"]):
        if link["state"] != "open" or link["ref"] in seen:
            continue
        try:
            issue = await tracker.read(link["ref"])
        except GitHubAppError as err:
            result.errors.append(f"{link['ref']}: {err}")
            continue
        if issue.state == "closed":
            await asyncio.to_thread(_set_state, link["ref"], "closed")
            events.emit(
                "issue_closed",
                {"ref": link["ref"]},
                task_id=link["task_id"],
                actor="planner",
                event_id=stable_event_id("issue_closed", link["ref"]),
            )
            result.closed.append(link["ref"])


async def _release_unblocked(goal: dict, tracker: Tracker, tasks: TaskClient, events: Events, result: SweepResult):
    for link in await asyncio.to_thread(_links, goal["id"]):
        if not link["task_id"] or not link["depends_on"] or link["state"] != "open":
            continue
        try:
            task = await asyncio.to_thread(tasks.get, link["task_id"])
        except TaskApiError as err:
            result.errors.append(f"{link['task_id']}: {err}")
            continue
        if task["status"] != "blocked":
            continue
        if any([await _is_open(ref, tracker) for ref in link["depends_on"]]):
            continue
        await asyncio.to_thread(tasks.set_status, link["task_id"], "queued", "every issue it depends on is closed")
        events.emit(
            "task_unblocked",
            {"ref": link["ref"]},
            task_id=link["task_id"],
            actor="planner",
            event_id=stable_event_id("task_unblocked", link["task_id"]),
        )
        result.unblocked.append(link["ref"])


async def _watch_pull_requests(goal: dict, tasks: TaskClient, events: Events, host: CodeHost, result: SweepResult):
    """Record what became of each open pull request no workflow watches, and report its merge or close."""
    try:
        waiting = await asyncio.to_thread(tasks.list, PR_WATCHED_STATUSES, goal["id"], PR_WATCH_LIMIT)
    except TaskApiError as err:
        result.errors.append(f"{goal['id']}: {err}")
        return
    for task in waiting:
        if not task.get("pr_url"):
            continue
        try:
            repo, number = code_host.pr_number(task["pr_url"])
        except CodeHostError:
            # A local repo's pull request is a file, with no code host to read it from.
            continue
        try:
            pull = await host.read_pull_request(repo, number)
            if pull.state in ("open", "draft"):
                await _record_checks(goal, task, pull, host, events)
            else:
                await _record_end(goal, task, pull, tasks, events, result)
        except (CodeHostError, TaskApiError) as err:
            result.errors.append(f"{task['id']}: {err}")


async def _record_checks(goal: dict, task: dict, pull: PullRequest, host: CodeHost, events: Events) -> None:
    """Record the head commit's checks once they settle: pending checks are recorded when they end."""
    if not pull.head_sha:
        return
    checks = code_host.checks_state(await host.check_runs(pull.repo, pull.head_sha))
    if checks.state not in ("passed", "failed"):
        return
    events.emit(
        "pull_request_checks",
        {"head_sha": pull.head_sha, "state": checks.state, "failed": checks.failed},
        task_id=task["id"],
        actor="planner",
        tags={"goal_id": goal["id"]},
        # The workflow's event id for the same fact, so the event service keeps one, whoever saw it first.
        event_id=stable_event_id(task["id"], "pull_request_checks", pull.head_sha),
    )


async def _record_end(
    goal: dict, task: dict, pull: PullRequest, tasks: TaskClient, events: Events, result: SweepResult
) -> None:
    """A merged pull request closes its task; one closed without merging cancels it."""
    if pull.state == "merged":
        event_type, status, reason = "pull_request_merged", "closed", "its pull request merged"
        payload = {"pr_url": task["pr_url"], "head_sha": pull.head_sha, "merge_sha": pull.merge_sha}
    else:
        event_type, status, reason = "pull_request_closed", "canceled", "its pull request was closed without merging"
        payload = {"pr_url": task["pr_url"], "head_sha": pull.head_sha}
    events.emit(
        event_type,
        payload,
        task_id=task["id"],
        actor="planner",
        tags={"goal_id": goal["id"]},
        event_id=stable_event_id(task["id"], event_type),
    )
    await asyncio.to_thread(tasks.set_status, task["id"], status, reason)
    (result.merged if status == "closed" else result.canceled).append(task["id"])


async def _is_open(ref: str, tracker: Tracker) -> bool:
    known = await asyncio.to_thread(_link, ref)
    if known and known["state"] == "closed":
        return False
    try:
        return (await tracker.read(ref)).state == "open"
    except (GitHubAppError, ValueError):
        # An issue the app cannot read still blocks, so a typo never lets work run early.
        return True


def _link(ref: str) -> dict | None:
    with db.connect(autocommit=True) as conn:
        return store.link(conn, ref)


def _links(goal_id: str) -> list[dict]:
    with db.connect(autocommit=True) as conn:
        return store.links(conn, goal_id)


def _save(ref, goal_id, repo, state, depends_on, task_id, refused_reason) -> None:
    with db.connect(autocommit=True) as conn:
        store.save_link(conn, ref, goal_id, repo, state, depends_on, task_id, refused_reason)


def _complete(goal_id: str) -> tuple[dict, dict]:
    with db.connect(autocommit=True) as conn:
        return store.update(conn, goal_id, only_if="active", status="met")


def _set_state(ref: str, state: str) -> None:
    with db.connect(autocommit=True) as conn:
        store.set_link_state(conn, ref, state)


def active_goals() -> list[dict]:
    with db.connect(autocommit=True) as conn:
        return store.list_goals(conn, ["active"])


async def sweep_active(
    tracker: Tracker, tasks: TaskClient, events: Events, host: CodeHost | None = None
) -> list[SweepResult]:
    goals = await asyncio.to_thread(active_goals)
    return [await sweep_goal(goal, tracker, tasks, events, host) for goal in goals]
