"""The goal loop's intake: turn a goal's labeled issues into tasks, and release tasks whose dependencies closed.

For each active goal, a sweep lists the open issues with the goal's label in
each of its repos. An issue the planner has not seen becomes a task through
the Task API, with `external_ref` `github:<owner>/<repo>#<number>`, the
issue's text, its `## Acceptance checks` (else the repo registry's default
checks), and the open issues it depends on. The task service starts a task
with open dependencies in `blocked`. A sweep also records issues that closed,
and moves a blocked task to `queued` once every issue it depends on is
closed. An issue already in another goal stays there.

The planner runs a sweep on every active goal every MH_PLANNER_SWEEP_SECONDS,
and right after a goal is created. Webhooks make it immediate later.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol

from meta_harness import db
from meta_harness.events.client import stable_event_id
from meta_harness.github_app import GitHubAppError
from meta_harness.planner import repos, store
from meta_harness.planner.tracker import Issue, Tracker
from meta_harness.tasks.client import TaskApiError, TaskClient

TASK_TYPES = {"bug", "feature", "refactor", "dependency", "test", "security", "docs"}


class Events(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any] | None = None, **fields: Any) -> str: ...


@dataclass
class SweepResult:
    goal_id: str
    joined: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    unblocked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def task_type_for(issue: Issue) -> str:
    for label in issue.labels:
        kind = label.removeprefix("type:")
        if label.startswith("type:") and kind in TASK_TYPES:
            return kind
    return "bug"


async def sweep_goal(goal: dict, tracker: Tracker, tasks: TaskClient, events: Events) -> SweepResult:
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
    await _record_closed(goal, seen, tracker, events, result)
    await _release_unblocked(goal, tracker, tasks, events, result)
    return result


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


def _set_state(ref: str, state: str) -> None:
    with db.connect(autocommit=True) as conn:
        store.set_link_state(conn, ref, state)


def active_goals() -> list[dict]:
    with db.connect(autocommit=True) as conn:
        return store.list_goals(conn, ["active"])


async def sweep_active(tracker: Tracker, tasks: TaskClient, events: Events) -> list[SweepResult]:
    goals = await asyncio.to_thread(active_goals)
    return [await sweep_goal(goal, tracker, tasks, events) for goal in goals]
