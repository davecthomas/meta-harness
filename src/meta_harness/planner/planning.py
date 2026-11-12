"""The planning step of a feature goal: a planning task drafts the goal's issues, and nothing else runs until
an operator approves the plan.

A feature goal starts in `planning`. The planner starts one task with task_type `plan`, pinned to the planning
candidate config/planner.json names (code names no profile and no model). Its run reads the goal's repos and
calls create_issue once per issue; each call is an issue_drafted event. When the task ends, those drafts
become the goal's plan, version 1. An operator edits it (each save a new version), asks for a new plan with a
note (a new planning task), or approves it. On approval the planner creates the issues on the code host, in
dependency order, with the goal's label, and the goal becomes active: its sweep takes the issues in as it
takes any labeled issue.

Only the control plane writes to the code host: the planning run proposes, and this module creates.
"""

import asyncio
import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from meta_harness import db
from meta_harness.github_app import GitHubAppError
from meta_harness.planner import plans, store
from meta_harness.planner.drafting import PLANNER_CONFIG_PATH
from meta_harness.planner.tracker import Issue, ref_for
from meta_harness.run_service import writing
from meta_harness.tasks.client import TaskApiError, TaskClient

log = logging.getLogger("meta_harness.planner")

# A planning task whose status is one of these has ended, and its drafts are read (closed) or it failed.
ENDED = {"closed", "failed", "canceled", "ready", "review_unresolved"}


class PlanError(RuntimeError):
    """The plan cannot be used as asked. The message says why, for the operator."""


class Events(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any] | None = None, **fields: Any) -> str: ...

    def read(self, from_position: int = 0, limit: int = 500, **filters: str) -> dict[str, Any]: ...


class IssueWriter(Protocol):
    async def ensure_label(self, repo: str, label: str) -> None: ...

    async def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> Issue: ...


def settings() -> dict[str, Any]:
    """config/planner.json's planning: the candidate a planning task is pinned to, and its spend limit."""
    return json.loads(PLANNER_CONFIG_PATH.read_text(encoding="utf-8"))["planning"]


def check_settings() -> None:
    """Raises RuntimeError naming what is wrong with config/planner.json's planning."""
    try:
        config = settings()
    except (OSError, ValueError, KeyError) as err:
        raise RuntimeError(f"{PLANNER_CONFIG_PATH.name}: no planning settings ({err})") from err
    candidate = config.get("candidate")
    if not isinstance(candidate, str) or not candidate.startswith("harness:") or "/" not in candidate:
        raise RuntimeError(f"{PLANNER_CONFIG_PATH.name}: planning.candidate must be harness:<profile>/<engine>:<model>")
    try:
        if Decimal(str(config.get("max_usd"))) <= 0:
            raise InvalidOperation
    except InvalidOperation as err:
        raise RuntimeError(f"{PLANNER_CONFIG_PATH.name}: planning.max_usd must be an amount above 0") from err


def goal_label(goal: dict) -> str:
    """The label a feature goal with none of its own gets: its plan's issues carry it, and the operator can
    put it on more issues by hand."""
    return f"meta-harness:goal-{goal['id'][:8]}"


def prompt(goal: dict, note: str | None, previous: list[dict[str, Any]] | None) -> str:
    """What the planning task is asked: the goal, its repos, and the rules for drafts. A replan adds the
    operator's note and the plan it replaces."""
    repos = ", ".join(goal["repos"])
    lines = [
        "Plan this goal as issues. Read the repository to see what exists, then call create_issue once for each",
        "issue the goal needs. Do not change any file: you only propose.",
        "",
        "--- goal ---",
        goal.get("title") or "",
        "",
        goal["statement"],
        "--- end of goal ---",
        "",
        f"The goal's repositories: {repos}. /workspace holds {goal['repos'][0]}. Every draft names one of them.",
        "",
        "Rules for the drafts:",
        "- One issue is one change a single pull request can make and its tests can prove. Prefer several small",
        "  issues to one large one.",
        "- Each body says what to build and why, then states its checks under a '## Acceptance checks' heading,",
        "  as a list of commands that pass when the issue is done.",
        "- Each body names the paths it changes on a 'Paths:' line, so issues that touch different paths can run",
        "  side by side.",
        "- depends_on names the drafts (by draft_id) and existing issues (owner/repo#number) that must be done",
        "  first. Name a dependency only when the work truly needs it.",
        "- Finish with a short summary of the plan: the issues in order, and anything you assumed.",
    ]
    if note:
        lines += ["", "An operator asked for this plan to replace the one before it, with this note:", note]
    if previous:
        lines += ["", "The plan before it, for reference:"]
        lines += [f"- {d['draft_id']} ({d['repo']}): {d['title']}" for d in previous]
    return "\n".join(lines).strip()


def _plan_count(goal_id: str) -> int:
    with db.connect(autocommit=True) as conn:
        latest = plans.latest(conn, goal_id)
    return latest["version"] if latest else 0


async def start(goal: dict, tasks: TaskClient, events: Events, who: str, note: str | None = None) -> dict:
    """Starts the goal's planning task and records it on the goal. Returns the task."""
    config = settings()

    def previous() -> list[dict[str, Any]] | None:
        with db.connect(autocommit=True) as conn:
            latest = plans.latest(conn, goal["id"])
        return latest["drafts"] if latest else None

    before = await asyncio.to_thread(previous)
    number = await asyncio.to_thread(_plan_count, goal["id"]) + 1
    try:
        task = await asyncio.to_thread(
            tasks.create,
            # One planning task per plan asked for: a retry of this call finds the same task.
            external_ref=f"plan:{goal['id']}:{number}:{len(note or '')}",
            repo=goal["repos"][0],
            task_type="plan",
            priority=goal["priority"],
            title=f"Plan: {goal.get('title') or goal['statement']}"[:300],
            description=prompt(goal, note, before),
            acceptance_checks=[],
            goal_id=goal["id"],
            pin=config["candidate"],
            max_usd=str(config["max_usd"]),
        )
    except TaskApiError as err:
        raise PlanError(f"could not start the planning task: {err}") from err

    def record() -> None:
        with db.connect(autocommit=True) as conn:
            plans.set_plan_task(conn, goal["id"], task["id"])

    await asyncio.to_thread(record)
    events.emit(
        "plan_requested",
        {"goal_id": goal["id"], "task_id": task["id"], "note": note},
        actor=who,
        tags={"goal_id": goal["id"]},
    )
    return task


async def collect(goal: dict, tasks: TaskClient, events: Events) -> dict | None:
    """Turns an ended planning task's drafts into the goal's next plan version. Returns the plan saved, or None
    when the goal has no planning task, the task still runs, or its plan is saved already.

    A task that failed, or drafted nothing, saves a plan with no drafts and the reason, so the operator sees
    why and can ask for a new one.
    """
    task_id = goal.get("plan_task_id")
    if not task_id:
        return None
    try:
        task = await asyncio.to_thread(tasks.get, task_id)
    except TaskApiError as err:
        log.warning("could not read planning task %s of goal %s: %s", task_id, goal["id"], err)
        return None
    if task["status"] not in ENDED:
        return None

    def read() -> list[dict[str, Any]]:
        return events.read(0, 5000, task_id=task_id, event_type="issue_drafted")["events"]

    drafts = plans.latest_drafts(await asyncio.to_thread(read))
    if task["status"] != "closed":
        invalid = [f"the planning task ended {task['status']}: {task.get('status_reason') or 'no reason was recorded'}"]
    else:
        invalid = plans.validate(drafts, goal["repos"])

    def save() -> dict | None:
        with db.connect(autocommit=True) as conn:
            if plans.from_task(conn, goal["id"], task_id):
                return None
            plan = plans.save(conn, goal["id"], drafts, invalid, "planned", "planner", task_id=task_id)
            plans.set_plan_task(conn, goal["id"], None)
            return plan

    plan = await asyncio.to_thread(save)
    if plan is None:
        return None
    _emit_saved(events, plan, "planner")
    return plan


def _emit_saved(events: Events, plan: dict, who: str) -> None:
    events.emit(
        "plan_saved",
        {
            "goal_id": plan["goal_id"],
            "version": plan["version"],
            "source": plan["source"],
            "drafts": len(plan["drafts"]),
            "invalid": plan["invalid"],
        },
        actor=who,
        tags={"goal_id": plan["goal_id"]},
    )


def edit(goal: dict, drafts: list[dict[str, Any]], who: str, events: Events) -> dict:
    """Saves an operator's edited drafts as the goal's next plan version, valid or not."""
    cleaned = [plans.clean(d) for d in drafts]
    invalid = plans.validate(cleaned, goal["repos"])
    with db.connect(autocommit=True) as conn:
        plan = plans.save(conn, goal["id"], cleaned, invalid, "edited", who)
    _emit_saved(events, plan, who)
    return plan


def issue_body(draft: dict[str, Any], created: dict[str, str]) -> str:
    """A draft's body as its issue's: with a Depends on: line naming the issues its dependencies became."""
    names = []
    for dep in draft["depends_on"]:
        if dep in created:
            repo, _, number = created[dep].removeprefix("github:").partition("#")
            names.append(f"#{number}" if repo.lower() == draft["repo"].lower() else f"{repo}#{number}")
        else:
            names.append(dep)
    body = draft["body"].rstrip()
    return f"{body}\n\nDepends on: {', '.join(names)}\n" if names else f"{body}\n"


async def approve(goal: dict, writer: IssueWriter, events: Events, who: str) -> dict:
    """Creates the plan's issues on the code host, in dependency order, with the goal's label, and holds each
    for the goal. Returns the plan with the issue each draft became.

    Each issue is noted as soon as it exists, so an approval the code host interrupted is asked again and
    carries on from the next draft, making none twice.

    Raises:
        PlanError: The goal has no plan, the plan is invalid, or the code host refused: its reason is given.
    """

    def load() -> dict | None:
        with db.connect(autocommit=True) as conn:
            return plans.latest(conn, goal["id"])

    plan = await asyncio.to_thread(load)
    if plan is None:
        raise PlanError("the goal has no plan yet: its planning task has not ended")
    if plan["invalid"]:
        raise PlanError(f"the plan cannot be approved: {'; '.join(plan['invalid'])}")
    label = goal["label"] or goal_label(goal)
    created: dict[str, str] = dict(plan["created"])
    try:
        for repo in sorted({d["repo"] for d in plan["drafts"]}):
            await writer.ensure_label(repo, label)
        for draft in plans.ordered(plan["drafts"]):
            if draft["draft_id"] in created:
                continue
            labels = list(dict.fromkeys([label, *draft["labels"]]))
            # The draft's body goes through the writing pass before it becomes an issue. The plan keeps the
            # draft as it was approved; the pass's record says what changed.
            [checked] = await writing.check_all([draft["body"]])
            if payload := writing.record("issue", [checked], goal_id=goal["id"], draft_id=draft["draft_id"]):
                events.emit("writing_checked", payload, actor="planner", tags={"goal_id": goal["id"]})
            written = {**draft, "body": checked.text} if checked else draft
            issue = await writer.create_issue(draft["repo"], draft["title"], issue_body(written, created), labels)
            ref = issue.ref or ref_for(draft["repo"], issue.number)
            created[draft["draft_id"]] = ref

            def note(draft_id: str = draft["draft_id"], ref: str = ref, repo: str = draft["repo"]) -> None:
                with db.connect(autocommit=True) as conn:
                    plans.record_created(conn, goal["id"], plan["version"], draft_id, ref)
                    store.add_refs(conn, goal["id"], [(ref, repo)])

            await asyncio.to_thread(note)
            events.emit(
                "issue_added",
                {"ref": ref, "goal_id": goal["id"]},
                actor=who,
                tags={"goal_id": goal["id"]},
            )
    except GitHubAppError as err:
        made = f" after creating {len(created)} of {len(plan['drafts'])} issues" if created else ""
        raise PlanError(f"GitHub refused{made}: {err}. Approve again to carry on") from err
    events.emit(
        "plan_approved",
        {"goal_id": goal["id"], "version": plan["version"], "issues": list(created.values())},
        actor=who,
        tags={"goal_id": goal["id"]},
    )
    return {**plan, "created": created}
