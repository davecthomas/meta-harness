"""The goal loop's intake: turn a goal's labeled issues into tasks, and release tasks whose dependencies closed.

For each active goal, a sweep lists the open issues with the goal's label in
each of its repos. An issue the planner has not seen becomes a task through
the Task API, with `external_ref` `github:<owner>/<repo>#<number>`, the
issue's text, its `## Acceptance checks` (else the repo registry's default
checks), and the open issues it depends on. The task service starts a task
with open dependencies in `blocked`. A sweep also records issues that closed,
and moves a blocked task to `queued` once every issue it depends on is
closed. An issue already in another goal stays there. An issue with no
acceptance checks, and no default checks for its repo, is refused, and looked
at again on every sweep, so adding the checks is all it takes. Each sweep refreshes
the title and labels it keeps of every issue it holds that has a task, open
or closed, so costs by label follow a relabel.

An issue closed by someone else's pull request, or by hand, has no work
left: a sweep cancels its task when the task has not opened a pull request
of its own (queued, blocked, or running with none), so the goal never pays
to redo finished work and the task frees its repo area.

A sweep also watches the pull request of each of the goal's tasks that ended
`ready` or `review_unresolved`, which no RunTask workflow watches any more. It
reads the pull request and its head commit's checks from GitHub, records each
change as an event (`pull_request_checks` once a new head commit's checks
settle, `pull_request_merged`, `pull_request_closed`) with the event id the
workflow uses for the same fact, so the fact is recorded once whoever sees it,
and reports a merge to the task service as `closed` and a close without
merging as `canceled`. It reads too the pull request of each of the goal's
`canceled` tasks that left one open, which nothing else watches: an operator
can cancel a task and merge or close its pull request by hand later. Its end
is recorded the same way, once, and the task stays canceled, so costs by
outcome see it (cost_facts.py).

Last, a sweep checks whether the goal is met: it has held at least one issue,
every issue it holds is closed, its label has no open issue it could take in,
and none of its tasks is queued, blocked, running or waiting on an operator.
A met goal moves to `met` and the planner emits `goal_met`. Only an `active`
goal is checked, so a paused goal, and one saved without starting, is left
alone.

A goal with a budget then has its spend looked at (budget_api.check): the
spend is saved on the goal, `goal_budget_reached` is recorded the first time
it reaches the budget, and a `block` goal moves to `budget_exhausted` at its
blocking line. A `budget_exhausted` goal is swept like an active one, so its
pull requests are still watched and it returns to `active` once its line is
above what it has spent.

The planner runs a sweep on every active goal every MH_PLANNER_SWEEP_SECONDS,
and right after a goal first starts: when it is created, or on its first
resume when it was saved without starting. Webhooks make it immediate later.

A goal no sweep covers any more, met, canceled, paused or stalled, is
followed up every FOLLOW_UP_SECONDS instead (follow_up_goal): the title and
labels of each issue it holds that has a task are refreshed, and its canceled
tasks' pull requests watched for their end, so costs by label and by outcome
stay true after the goal ends. It also records each issue the goal holds as
open that has closed (refresh_states), which the planner does too when the
goal's page is read, so the goal's record of its issues does not stop at its
last sweep. It takes in nothing and changes no task. The
canceled tasks of no goal, which no goal's sweep lists, have their pull
requests watched as often (follow_up_goalless).
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol

from meta_harness import db
from meta_harness.events.client import stable_event_id
from meta_harness.github_app import GitHubAppError
from meta_harness.planner import budget_api, cost_facts, repos, store
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
# The goals no sweep covers, which a follow-up looks at instead, and how often.
FOLLOWED_UP_STATUSES = ["met", "canceled", "paused", "stalled"]
FOLLOW_UP_SECONDS = 3600.0


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
    # Task ids canceled because their issue was closed with their work not yet published.
    stale: list[str] = field(default_factory=list)
    # Task ids whose pull request the sweep found merged, or closed without merging.
    merged: list[str] = field(default_factory=list)
    canceled: list[str] = field(default_factory=list)
    # Task ids, canceled with their pull request open, whose pull request the sweep found merged or closed.
    ended: list[str] = field(default_factory=list)
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
    # A goal with no label takes only the issues an operator added by ref: listing with no label would
    # list every open issue in the repo.
    for repo in goal["repos"] if goal["label"] else []:
        try:
            issues = await tracker.list_open(repo, goal["label"])
        except GitHubAppError as err:
            result.errors.append(f"{repo}: {err}")
            continue
        for issue in issues:
            seen.add(issue.ref)
            await _take_in(goal, issue, tracker, tasks, events, result)
    await _take_in_added(goal, seen, tracker, tasks, events, result)
    await _watch_pull_requests(goal, tasks, events, host or code_host, result)
    await _watch_canceled_pull_requests(goal, tasks, events, host or code_host, result)
    await _record_closed(goal, seen, tracker, events, result)
    await _cancel_for_closed_issues(goal, tasks, result)
    await _release_unblocked(goal, tracker, tasks, events, result)
    if not result.errors:
        await _complete_if_met(goal, tracker, tasks, events, result)
    try:
        await budget_api.check(goal, tasks, events)
    except Exception as err:  # noqa: BLE001 - an unreadable spend is this sweep's error; the next one looks again
        result.errors.append(f"{goal['id']}: could not check the budget: {err}")
    return result


async def follow_up_goal(
    goal: dict, tracker: Tracker, tasks: TaskClient, events: Events, host: CodeHost | None = None
) -> SweepResult:
    """For a goal no sweep covers any more: refresh the title and labels of each issue it holds that has a
    task, record each open one that has closed, and record the end of each pull request its canceled tasks
    left open. Nothing is taken in, and no task or goal changes status."""
    result = SweepResult(goal["id"])
    for link in await asyncio.to_thread(_links, goal["id"]):
        if not link["task_id"]:
            continue
        try:
            issue = await tracker.read(link["ref"])
        except (GitHubAppError, ValueError) as err:
            # One that cannot be read keeps the labels it had.
            result.errors.append(f"{link['ref']}: {err}")
            continue
        await _refresh(link, issue)
    result.errors.extend(await refresh_states(goal, tracker, events, result))
    await _watch_canceled_pull_requests(goal, tasks, events, host or code_host, result)
    return result


async def refresh_states(goal: dict, tracker: Tracker, events: Events, result: SweepResult | None = None) -> list[str]:
    """Record each issue the goal holds as open that has closed on the tracker, as a sweep would, for a goal
    no sweep covers: without it the goal's record keeps the state its last sweep saw, and its page shows a
    closed issue as open. Returns what could not be read; those keep the state they had."""
    errors: list[str] = []
    for link in await asyncio.to_thread(_links, goal["id"]):
        if link["state"] != "open":
            continue
        try:
            issue = await tracker.read(link["ref"])
        except (GitHubAppError, ValueError) as err:
            errors.append(f"{link['ref']}: {err}")
            continue
        if issue.state != "closed":
            continue
        await asyncio.to_thread(_set_state, link["ref"], "closed")
        events.emit(
            "issue_closed",
            {"ref": link["ref"]},
            task_id=link["task_id"],
            actor="planner",
            event_id=stable_event_id("issue_closed", link["ref"]),
        )
        if result is not None:
            result.closed.append(link["ref"])
    return errors


# Why a task ended when its goal was canceled: each task's task_status_changed carries it.
GOAL_CANCELED_REASON = "its goal was canceled"
# A task in one of these has a pull request open that canceling its goal leaves for the operator.
LEFT_WITH_PULL_REQUEST = ["running", "ready", "review_unresolved"]


async def cancel_goal_tasks(goal: dict, tasks: TaskClient, who: str) -> dict[str, list[str]]:
    """End what a canceled goal has not started: cancel each of its queued and blocked tasks, and ask each
    running one with no pull request to stop, as a task cancel does. A task with a pull request open is left
    for the operator, who decides what becomes of the pull request. Returns the task ids by what was done:
    canceled, stopping, left (a pull request open), and errors for those the task service refused."""
    done: dict[str, list[str]] = {"canceled": [], "stopping": [], "left": [], "errors": []}
    try:
        found = await asyncio.to_thread(
            tasks.list, ["queued", "blocked", *LEFT_WITH_PULL_REQUEST], goal["id"], PR_WATCH_LIMIT
        )
    except TaskApiError as err:
        done["errors"].append(f"{goal['id']}: {err}")
        return done
    for task in found:
        if task.get("pr_url"):
            done["left"].append(task["id"])
            continue
        if task["status"] not in UNSTARTED_OR_RUNNING:
            # Finished work with no pull request on a code host (a local repo's): nothing to stop.
            continue
        if task.get("cancel_requested"):
            done["stopping"].append(task["id"])
            continue
        try:
            ended = await asyncio.to_thread(tasks.cancel, task["id"], GOAL_CANCELED_REASON, who)
        except TaskApiError as err:
            done["errors"].append(f"{task['id']}: {err}")
            continue
        done["canceled" if ended["status"] == "canceled" else "stopping"].append(task["id"])
    return done


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
    for repo in goal["repos"] if goal["label"] else []:
        try:
            issues = await tracker.list_open(repo, goal["label"])
        except GitHubAppError as err:
            result.errors.append(f"{repo}: {err}")
            return
        for issue in issues:
            existing = await asyncio.to_thread(_link, issue.ref)
            mine = existing is not None and existing["goal_id"] == goal["id"]
            removed = existing is not None and bool(existing.get("dropped"))
            # An open issue this goal holds, or one it could take in, keeps it open. One an operator removed
            # from this goal does not: the goal never takes it again. One another goal removed is free to take.
            if existing is None or (mine and not removed) or (removed and not mine):
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


async def _take_in_added(
    goal: dict, seen: set[str], tracker: Tracker, tasks: TaskClient, events: Events, result: SweepResult
):
    """Take in the issues an operator added to the goal by ref, which its label did not list. One in a repo the
    goal no longer names makes no task there."""
    names = {name.lower() for name in goal["repos"]}
    for link in await asyncio.to_thread(_links, goal["id"]):
        # A refused issue is read again each sweep: it may have been given its acceptance checks since.
        if link["task_id"] or link["state"] != "open" or link["ref"] in seen:
            continue
        if link["repo"].lower() not in names:
            continue
        try:
            issue = await tracker.read(link["ref"])
        except (GitHubAppError, ValueError) as err:
            result.errors.append(f"{link['ref']}: {err}")
            continue
        seen.add(issue.ref)
        if issue.state == "open":
            await _take_in(goal, issue, tracker, tasks, events, result)


async def _take_in(goal: dict, issue: Issue, tracker: Tracker, tasks: TaskClient, events: Events, result: SweepResult):
    existing = await asyncio.to_thread(_link, issue.ref)
    if existing and existing.get("dropped"):
        # An operator removed the issue from its goal. That goal never takes it in again by its label;
        # another goal takes it as an issue it has not seen.
        if existing["goal_id"] == goal["id"]:
            return
        await asyncio.to_thread(_forget_link, issue.ref)
        existing = None
    title = issue.title[:300]
    labels = list(dict.fromkeys(issue.labels))
    if existing and existing["goal_id"] == goal["id"] and existing.get("title") != title:
        await asyncio.to_thread(_set_title, issue.ref, title)
    if existing and existing["goal_id"] == goal["id"] and existing.get("labels") != labels:
        await asyncio.to_thread(_set_labels, issue.ref, labels)
    if existing and (existing["goal_id"] != goal["id"] or existing["task_id"]):
        return
    # An issue refused before is looked at again each sweep, so one given its acceptance checks since, or
    # whose repo was given default checks, becomes a task without anyone dropping and adding it.
    registered = repos.get(issue.repo)
    checks = issue.checks or (registered.default_checks if registered else [])
    if not checks:
        reason = "the issue has no ## Acceptance checks and the repo registry names no default checks"
        if existing and existing.get("refused_reason") == reason:
            result.refused.append(issue.ref)
            return
        await asyncio.to_thread(
            _save, issue.ref, goal["id"], issue.repo, "open", issue.depends_on, None, reason, title, labels
        )
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
            paths=issue.paths or None,
        )
    except TaskApiError as err:
        result.errors.append(f"{issue.ref}: {err}")
        return
    await asyncio.to_thread(
        _save, issue.ref, goal["id"], issue.repo, "open", open_deps, task["id"], None, title, labels
    )
    events.emit(
        "issue_joined",
        {"ref": issue.ref, "goal_id": goal["id"], "depends_on": open_deps, "status": task["status"]},
        task_id=task["id"],
        actor="planner",
        event_id=stable_event_id("issue_joined", issue.ref, goal["id"]),
    )
    result.joined.append(issue.ref)


async def _record_closed(goal: dict, seen: set[str], tracker: Tracker, events: Events, result: SweepResult) -> None:
    """Record each held issue the label's listing left out that has closed, and refresh the title and labels
    of every one the listing left out: one taken in already, one closed, one an operator added by ref. Costs by
    label read them (cost_facts.py), so a relabel shows even after the issue was taken in or closed."""
    for link in await asyncio.to_thread(_links, goal["id"]):
        if link["ref"] in seen or (link["state"] != "open" and not link["task_id"]):
            continue
        try:
            issue = await tracker.read(link["ref"])
        except GitHubAppError as err:
            # A closed issue is read only for its labels: one that cannot be read keeps those it had.
            if link["state"] == "open":
                result.errors.append(f"{link['ref']}: {err}")
            continue
        await _refresh(link, issue)
        if link["state"] == "open" and issue.state == "closed":
            await asyncio.to_thread(_set_state, link["ref"], "closed")
            events.emit(
                "issue_closed",
                {"ref": link["ref"]},
                task_id=link["task_id"],
                actor="planner",
                event_id=stable_event_id("issue_closed", link["ref"]),
            )
            result.closed.append(link["ref"])


async def _refresh(link: dict, issue: Issue) -> None:
    """Save the issue's title and labels on its link where they changed."""
    title = issue.title[:300]
    labels = list(dict.fromkeys(issue.labels))
    if link.get("title") != title:
        await asyncio.to_thread(_set_title, issue.ref, title)
    if link.get("labels") != labels:
        await asyncio.to_thread(_set_labels, issue.ref, labels)


# A task in one of these has not ended, so an issue closed under it may leave it with nothing to do.
UNSTARTED_OR_RUNNING = ["queued", "blocked", "running"]


async def _cancel_for_closed_issues(goal: dict, tasks: TaskClient, result: SweepResult) -> None:
    """Cancel each of the goal's tasks whose issue is closed and that has no pull request of its own.

    The issue was closed some other way: another pull request, or by hand. A task with a pull request is left
    alone, since its own merge is what closes its issue, and its workflow or the watch above ends it.
    """
    closed = {
        link["task_id"]: link["ref"]
        for link in await asyncio.to_thread(_links, goal["id"])
        if link["state"] == "closed" and link["task_id"]
    }
    if not closed:
        return
    try:
        unfinished = await asyncio.to_thread(tasks.list, UNSTARTED_OR_RUNNING, goal["id"], PR_WATCH_LIMIT)
    except TaskApiError as err:
        result.errors.append(f"{goal['id']}: {err}")
        return
    for task in unfinished:
        if task["id"] not in closed or task.get("pr_url") or task.get("cancel_requested"):
            continue
        try:
            await asyncio.to_thread(
                tasks.cancel,
                task["id"],
                f"its issue {closed[task['id']].removeprefix('github:')} was closed",
                "planner",
            )
        except TaskApiError as err:
            result.errors.append(f"{task['id']}: {err}")
            continue
        result.stale.append(task["id"])


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


async def follow_up_goalless(tasks: TaskClient, events: Events, host: CodeHost | None = None) -> SweepResult:
    """Record the end of each pull request a canceled task of no goal left open. A task can be made without a
    goal, and no goal's sweep or follow-up lists it, so this watches those, as often as goals are followed up."""
    result = SweepResult("")
    await _watch_canceled_pull_requests(None, tasks, events, host or code_host, result)
    return result


async def _watch_canceled_pull_requests(
    goal: dict | None, tasks: TaskClient, events: Events, host: CodeHost, result: SweepResult
) -> None:
    """Record the end of each pull request a canceled task left open, which no workflow and no other watch
    reads: one merged or closed by hand after its task was canceled. The task stays canceled. Each is read
    until its end is recorded, then never again. Without a goal, the canceled tasks of no goal are watched."""
    try:
        if goal:
            canceled = await asyncio.to_thread(cost_facts.every_task, tasks, goal["id"], ["canceled"])
        else:
            every = await asyncio.to_thread(cost_facts.every_task, tasks, None, ["canceled"])
            canceled = [task for task in every if not task.get("goal_id")]
    except TaskApiError as err:
        result.errors.append(f"{goal['id'] if goal else 'tasks of no goal'}: {err}")
        return
    with_pull = [task for task in canceled if task.get("pr_url")]
    if not with_pull:
        return
    ended = await asyncio.to_thread(_ended, [task["id"] for task in with_pull])
    for task in with_pull:
        if task["id"] in ended:
            continue
        try:
            repo, number = code_host.pr_number(task["pr_url"])
        except CodeHostError:
            continue
        try:
            pull = await host.read_pull_request(repo, number)
        except CodeHostError as err:
            result.errors.append(f"{task['id']}: {err}")
            continue
        if pull.state in ("open", "draft"):
            continue
        _emit_end(goal, task, pull, events)
        await asyncio.to_thread(_save_end, task["id"], pull.state)
        result.ended.append(task["id"])


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
        status, reason = "closed", "its pull request merged"
    else:
        status, reason = "canceled", "its pull request was closed without merging"
    _emit_end(goal, task, pull, events)
    await asyncio.to_thread(_save_end, task["id"], pull.state)
    await asyncio.to_thread(tasks.set_status, task["id"], status, reason)
    (result.merged if status == "closed" else result.canceled).append(task["id"])


def _emit_end(goal: dict | None, task: dict, pull: PullRequest, events: Events) -> None:
    """Record a pull request's end, merged or closed without merging, as the run workflow does."""
    if pull.state == "merged":
        event_type = "pull_request_merged"
        payload = {"pr_url": task["pr_url"], "head_sha": pull.head_sha, "merge_sha": pull.merge_sha}
    else:
        event_type = "pull_request_closed"
        payload = {"pr_url": task["pr_url"], "head_sha": pull.head_sha}
    events.emit(
        event_type,
        payload,
        task_id=task["id"],
        actor="planner",
        tags={"goal_id": goal["id"]} if goal else {},
        event_id=stable_event_id(task["id"], event_type),
    )


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


def _save(ref, goal_id, repo, state, depends_on, task_id, refused_reason, title=None, labels=None) -> None:
    with db.connect(autocommit=True) as conn:
        store.save_link(conn, ref, goal_id, repo, state, depends_on, task_id, refused_reason, title, labels)


def _forget_link(ref: str) -> None:
    with db.connect(autocommit=True) as conn:
        store.forget_link(conn, ref)


def _set_title(ref: str, title: str) -> None:
    with db.connect(autocommit=True) as conn:
        store.set_link_title(conn, ref, title)


def _set_labels(ref: str, labels: list[str]) -> None:
    with db.connect(autocommit=True) as conn:
        store.set_link_labels(conn, ref, labels)


def _ended(task_ids: list[str]) -> set[str]:
    with db.connect(autocommit=True) as conn:
        return store.ended_pull_requests(conn, task_ids)


def _save_end(task_id: str, state: str) -> None:
    with db.connect(autocommit=True) as conn:
        store.save_pull_request_end(conn, task_id, "merged" if state == "merged" else "closed")


def _complete(goal_id: str) -> tuple[dict, dict]:
    with db.connect(autocommit=True) as conn:
        return store.update(conn, goal_id, only_if="active", status="met")


def _set_state(ref: str, state: str) -> None:
    with db.connect(autocommit=True) as conn:
        store.set_link_state(conn, ref, state)


def active_goals() -> list[dict]:
    with db.connect(autocommit=True) as conn:
        return store.list_goals(conn, ["active"])


def followed_up_goals() -> list[dict]:
    """The goals a follow-up covers: those no timed sweep does."""
    with db.connect(autocommit=True) as conn:
        return store.list_goals(conn, FOLLOWED_UP_STATUSES)


def swept_goals() -> list[dict]:
    """The goals a timed sweep covers: the active ones, and those stopped by their budget."""
    with db.connect(autocommit=True) as conn:
        return store.list_goals(conn, ["active", "budget_exhausted"])


async def sweep_active(
    tracker: Tracker, tasks: TaskClient, events: Events, host: CodeHost | None = None
) -> list[SweepResult]:
    goals = await asyncio.to_thread(active_goals)
    return [await sweep_goal(goal, tracker, tasks, events, host) for goal in goals]
