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
import logging
from typing import Any, Protocol

from meta_harness import outbound
from meta_harness import db
from meta_harness.config import kinds as config_kinds
from meta_harness.config import store as config_store
from meta_harness.planner import voice
from meta_harness.trackers.base import TrackerError
from meta_harness.planner import assignees, plans, splits, store
from meta_harness.planner.drafting import PLANNER_CONFIG_PATH
from meta_harness.planner.tracker import Issue, ref_for
from meta_harness.run_service import provenance, writing
from meta_harness.tasks.client import TaskApiError, TaskClient

log = logging.getLogger("meta_harness.planner")

# What a planning run records its plan as: a new task, a decision on an issue the goal had, and the summary.
PLAN_EVENTS = ("issue_drafted", "issue_planned", "plan_summarized")
# A planning task whose status is one of these has ended, and its drafts are read (closed) or it failed.
ENDED = {"closed", "failed", "canceled", "ready", "review_unresolved"}


# How a PlanError for a tracker's refusal starts: the API answers those 502, since asking again may work.
REFUSED = "the issue tracker refused"


class PlanError(RuntimeError):
    """The plan cannot be used as asked. The message says why, for the operator."""


class Events(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any] | None = None, **fields: Any) -> str: ...

    def read(self, from_position: int = 0, limit: int = 500, **filters: str) -> dict[str, Any]: ...


class IssueWriter(Protocol):
    async def ensure_label(self, repo: str, label: str) -> None: ...

    async def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> Issue: ...


# Which goals are planned before their tasks run: every goal, or only one that plans its issues from its
# description, as before every goal was planned.
EVERY_GOAL, FROM_DESCRIPTION = "all", "from_description"
GOALS = (EVERY_GOAL, FROM_DESCRIPTION)
# What a planning task is shown of the goal's issues when the configuration does not say: how many, and how
# much of each one's text.
MAX_ISSUES, ISSUE_CHARS = 60, 4000


def settings() -> dict[str, Any]:
    """The planning configuration (config/kinds.py's `planning`): the candidate a planning task is pinned to,
    its spend limit, which goals are planned (`goals`), how much of a goal's issues its planning task is shown,
    and whether a plan's new tasks go to the goal's tracker by default. The console's active version, or the
    planning section of config/planner.json."""
    return config_store.current_document("planning")


def plans_every_goal() -> bool:
    """Whether a goal that takes existing issues is planned too, before any of its tasks run."""
    return settings().get("goals", FROM_DESCRIPTION) == EVERY_GOAL


def tracks_new_tasks() -> bool:
    """Whether a plan's new tasks go to the goal's tracker unless the operator says otherwise."""
    return bool(settings().get("track_new_tasks", True))


def check_settings() -> None:
    """Raises RuntimeError naming what is wrong with the planning configuration."""
    try:
        config = settings()
    except (OSError, ValueError, KeyError) as err:
        raise RuntimeError(f"{PLANNER_CONFIG_PATH.name}: no planning settings ({err})") from err
    if reasons := config_kinds.check_planning(config, None):
        raise RuntimeError(f"the planning configuration ({PLANNER_CONFIG_PATH.name}'s planning): {'; '.join(reasons)}")


def goal_label(goal: dict) -> str:
    """The label a feature goal with none of its own gets: its plan's issues carry it, and the operator can
    put it on more issues by hand."""
    return f"meta-harness:goal-{goal['id'][:8]}"


def prompt(
    goal: dict, note: str | None, previous: list[dict[str, Any]] | None, issues: list[Issue] | None = None
) -> str:
    """What the planning task is asked: the goal, its repos, the issues it already has, and the rules for the
    plan. A replan adds the operator's note and the plan it replaces."""
    repos = ", ".join(goal["repos"])
    issues = issues or []
    lines = [
        "Plan this goal. Read the repository to see what exists. Do not change any file: you only propose, and",
        "you record the plan with your tools, not as prose.",
        "",
        "--- goal ---",
        goal.get("title") or "",
        "",
        goal["statement"],
        "--- end of goal ---",
        "",
        f"The goal's repositories: {repos}. /workspace holds {goal['repos'][0]}. Every new task names one of them.",
        "",
    ]
    if issues:
        config = settings()
        chars = int(config.get("issue_chars", ISSUE_CHARS))
        shown = issues[: int(config.get("max_issues", MAX_ISSUES))]
        lines += [
            f"The goal already has {len(issues)} issue{'' if len(issues) == 1 else 's'}, between the markers below."
            + (f" Only the first {len(shown)} are shown." if len(shown) < len(issues) else ""),
            "Read them together, as one body of work, and call plan_issue once for each with your decision:",
            "- keep: it runs as it is. Say what it waits on in depends_on, when it truly needs another task first.",
            "- split: it is too large for one pull request, or holds separate changes. Write the smaller tasks with",
            "  create_issue and name them in replaced_by. They run in its place.",
            "- leave_out: it duplicates another, is already done in the repository, or is not this goal's work.",
            "Give each decision its reason in a sentence. An issue you say nothing of is kept.",
            "Write a new task with create_issue only for work the goal needs that no issue covers, or for a split.",
            "",
            "--- issues ---",
        ]
        for issue in shown:
            body = (issue.body or "").strip()
            body = body[:chars] + " [cut]" if len(body) > chars else body
            lines += [f"## {issue.ref} ({issue.repo}): {issue.title}", body or "(no description)", ""]
        lines += ["--- end of issues ---", ""]
    else:
        lines += ["The goal has no issues yet: call create_issue once for each task it needs.", ""]
    lines += [
        "Rules for a new task:",
        "- One task is one change a single pull request can make and its tests can prove. Prefer several small",
        "  tasks to one large one.",
        "- Its body says what to build and why, then states its checks under a '## Acceptance checks' heading,",
        "  as a list of commands that pass when it is done.",
        "- Its body names the paths it changes on a 'Paths:' line: the files it changes, or the narrowest",
        "  directories that hold them. Never a whole top-level directory such as tests, src or console: tasks",
        "  whose paths overlap run one after another, so a broad path makes every other task wait for this one.",
        "- depends_on names what must be done first: a new task by its draft_id, an existing issue by its ref as",
        "  shown above. Name a dependency only when the work truly needs it: what does not wait runs in parallel.",
        "",
        "Finish by calling plan_summary: how the plan reaches the goal, for the operator to read before approving",
        "it, and as gaps what the operator should know or decide before approving: anything the goal asks for that",
        "no task covers, and any risk or assumption that could change their decision.",
        "The summary and the gaps are the only part of the plan written for the operator, and the operator is not",
        "reading the code. They decide in a few seconds whether to trust the plan, so write those two this way,",
        "whatever detail the tasks' own bodies need:",
        voice.instructions(settings(), "plan"),
    ]
    if note:
        lines += ["", "An operator asked for this plan to replace the one before it, with this note:", note]
    if previous:
        lines += ["", "The new tasks of the plan before it, for reference:"]
        lines += [f"- {d['draft_id']} ({d['repo']}): {d['title']}" for d in previous]
    return "\n".join(lines).strip()


def _plan_count(goal_id: str) -> int:
    with db.connect(autocommit=True) as conn:
        latest = plans.latest(conn, goal_id)
    return latest["version"] if latest else 0


async def start(
    goal: dict,
    tasks: TaskClient,
    events: Events,
    who: str,
    note: str | None = None,
    issues: list[Issue] | None = None,
    external_ref: str | None = None,
    record: bool = True,
) -> dict:
    """Starts the goal's planning task and records it on the goal, with the issues it was given to plan over
    (issues: those the goal already has). Returns the task. A suggested fix's planning task (suggestions.py)
    passes its own external_ref and record=False: the goal's plan is not being redrafted, so nothing waits on
    it."""
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
            external_ref=external_ref or f"plan:{goal['id']}:{number}:{len(note or '')}",
            repo=goal["repos"][0],
            task_type="plan",
            priority=goal["priority"],
            title=f"Plan: {goal.get('title') or goal['statement']}"[:300],
            description=prompt(goal, note, before, issues),
            acceptance_checks=[],
            goal_id=goal["id"],
            pin=config["candidate"],
            max_usd=str(config["max_usd"]),
        )
    except TaskApiError as err:
        raise PlanError(f"could not start the planning task: {err}") from err

    def on_goal() -> None:
        with db.connect(autocommit=True) as conn:
            plans.set_plan_task(conn, goal["id"], task["id"], [plans.clean_issue(vars(i)) for i in issues or []])

    if record:
        await asyncio.to_thread(on_goal)
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

    def issues_given() -> list[dict[str, Any]]:
        with db.connect(autocommit=True) as conn:
            return plans.plan_issues(conn, goal["id"])

    issues = await asyncio.to_thread(issues_given)
    found = await gather(goal, task, events, issues)
    drafts, items, summary, gaps, invalid = (found[k] for k in ("drafts", "items", "summary", "gaps", "invalid"))

    def save() -> dict | None:
        with db.connect(autocommit=True) as conn:
            if plans.from_task(conn, goal["id"], task_id):
                return None
            plan = plans.save(
                conn,
                goal["id"],
                drafts,
                invalid,
                "planned",
                "planner",
                task_id=task_id,
                items=items,
                summary=summary,
                gaps=gaps,
                issues=issues,
            )
            plans.set_plan_task(conn, goal["id"], None)
            return plan

    plan = await asyncio.to_thread(save)
    if plan is None:
        return None
    _emit_saved(events, plan, "planner")
    return plan


async def gather(goal: dict, task: dict, events: Events, issues: list[dict[str, Any]]) -> dict[str, Any]:
    """What an ended planning task recorded, as a plan: its drafts, its decisions on the issues it was given,
    its summary and gaps, and why the plan cannot be approved, if it cannot."""

    def read() -> list[dict[str, Any]]:
        return events.read(0, 5000, task_id=task["id"], event_type=",".join(PLAN_EVENTS))["events"]

    recorded = await asyncio.to_thread(read)

    def of(event_type: str) -> list[dict[str, Any]]:
        return [e for e in recorded if e["event_type"] == event_type]

    drafts = plans.latest_drafts(of("issue_drafted"))
    items = plans.complete(plans.latest_items(of("issue_planned")), issues)
    summary, gaps = plans.latest_summary(of("plan_summarized"))
    # A draft that names a whole top-level directory would make every task touching it wait for this one: a
    # gap the planner's suggested fix can settle by naming the files.
    gaps = [*gaps, *plans.broad_path_gaps(drafts)]
    if task["status"] != "closed":
        invalid = [f"the planning task ended {task['status']}: {task.get('status_reason') or 'no reason was recorded'}"]
    else:
        invalid = plans.validate(drafts, goal["repos"], items, issues)
    return {"drafts": drafts, "items": items, "summary": summary, "gaps": gaps, "invalid": invalid}


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


def edit(
    goal: dict,
    drafts: list[dict[str, Any]],
    who: str,
    events: Events,
    items: list[dict[str, Any]] | None = None,
) -> dict:
    """Saves an operator's edited plan as the goal's next version, valid or not: its new tasks, and its
    decisions on the goal's issues when given (items; None keeps those of the version before). The summary,
    the gaps and the issues the plan was made over are the planner's, and carry over."""
    cleaned = [plans.clean(d) for d in drafts]
    with db.connect(autocommit=True) as conn:
        before = plans.latest(conn, goal["id"]) or {}
        issues = list(before.get("issues") or [])
        decided = plans.complete(
            [plans.clean_item(i) for i in items] if items is not None else list(before.get("items") or []), issues
        )
        invalid = plans.validate(cleaned, goal["repos"], decided, issues)
        plan = plans.save(
            conn,
            goal["id"],
            cleaned,
            invalid,
            "edited",
            who,
            items=decided,
            summary=str(before.get("summary") or ""),
            gaps=list(before.get("gaps") or []),
            issues=issues,
        )
    _emit_saved(events, plan, who)
    return plan


def issue_body(draft: dict[str, Any], created: dict[str, str]) -> str:
    """A draft's body as its issue's: with a Depends on: line naming the issues its dependencies became."""
    names = []
    for dep in draft["depends_on"]:
        if dep in created and created[dep].startswith("github:"):
            repo, _, number = created[dep].removeprefix("github:").partition("#")
            names.append(f"#{number}" if repo.lower() == draft["repo"].lower() else f"{repo}#{number}")
        elif dep in created and created[dep].startswith("mh:"):
            # A task kept in the meta-harness is named by its whole ref, which is all it has.
            names.append(created[dep])
        elif dep in created:
            # Another tracker's issue is named by its key: jira:PROJ-12 as PROJ-12.
            names.append(created[dep].partition(":")[2])
        else:
            names.append(dep)
    body = draft["body"].rstrip()
    return f"{body}\n\nDepends on: {', '.join(names)}\n" if names else f"{body}\n"


async def approve(
    goal: dict, writer: IssueWriter, events: Events, who: str, tracked: bool = True, originals: Any = None
) -> dict:
    """Applies the goal's plan. Its new tasks become issues, in dependency order, with the goal's label, in
    the tracker `writer` writes to: the goal's own, or the meta-harness's (tracked says which, for the
    record). Then each decision on an issue the goal already had is settled: a kept one is taken into the goal
    with what the plan says it waits on, a split one is replaced by its new tasks, and one left out is removed.
    Returns the plan with the issue each new task became.

    Each issue is noted as soon as it exists, so an approval the tracker interrupted is asked again and
    carries on from the next task, making none twice. Settling a decision twice changes nothing.

    Raises:
        PlanError: The goal has no plan, the plan is invalid, or the tracker refused: its reason is given.
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
    # Each new task that takes the place of an issue the plan split, and that issue: its body says where it came from.
    origin = {
        draft_id: item["ref"]
        for item in plan.get("items") or []
        if item["decision"] == plans.SPLIT
        for draft_id in item["replaced_by"]
    }
    try:
        for repo in sorted({d["repo"] for d in plan["drafts"]}):
            await writer.ensure_label(repo, label)
        for draft in plans.ordered(plan["drafts"]):
            if draft["draft_id"] in created:
                continue
            labels = list(dict.fromkeys([label, *draft["labels"]]))
            # The draft's body goes through the writing pass before it becomes an issue. The plan keeps the
            # draft as it was approved; the pass's record says what changed.
            where = getattr(writer, "scheme", "github")
            checked = await outbound.for_tracker(draft["body"], where, outbound.BODY, outbound.GENERATED)
            payload = writing.record(
                "issue", outbound.writing_outcomes([checked]), goal_id=goal["id"], draft_id=draft["draft_id"]
            )
            if payload:
                events.emit("writing_checked", payload, actor="planner", tags={"goal_id": goal["id"]})
            text = issue_body({**draft, "body": str(checked)}, created)
            if draft["draft_id"] in origin:
                text = splits.with_origin(text, origin[draft["draft_id"]])
            # Last, after the writing pass: the issue says which goal's plan, drafted by which planning task and
            # approved by whom, it came from. The lines added here are fixed, so the whole goes out as composed.
            signed = provenance.Provenance(provenance.ISSUE, goal=goal["id"], task=plan.get("task_id") or "", by=who)
            text = await outbound.for_tracker(text, where, outbound.BODY, outbound.FIXED, provenance=signed)
            title = await outbound.for_tracker(draft["title"], where, outbound.TITLE, outbound.GENERATED)
            issue = await writer.create_issue(draft["repo"], title, text, labels)
            ref = issue.ref or ref_for(draft["repo"], issue.number)
            created[draft["draft_id"]] = ref

            # What it waits on, as refs: the plan's word, kept beside the Depends on: line in its text, which a
            # tracker reads only for its own kind of issue.
            waits = [created.get(dep, dep) for dep in draft["depends_on"]]

            def note(
                draft_id: str = draft["draft_id"], ref: str = ref, repo: str = draft["repo"], waits: list[str] = waits
            ) -> None:
                with db.connect(autocommit=True) as conn:
                    plans.record_created(conn, goal["id"], plan["version"], draft_id, ref)
                    store.add_refs(conn, goal["id"], [(ref, repo)])
                    store.plan_keep(conn, goal["id"], ref, repo, [w for w in waits if ":" in w])

            await asyncio.to_thread(note)
            events.emit(
                "issue_added",
                {"ref": ref, "goal_id": goal["id"]},
                actor=who,
                tags={"goal_id": goal["id"]},
            )
            # Assigned once it is noted, so a person the tracker refuses never costs the issue: it is reported.
            await assignees.assign_new(goal, writer, ref, draft["repo"], events)
    except TrackerError as err:
        made = f" after creating {len(created)} of {len(plan['drafts'])} issues" if created else ""
        raise PlanError(f"{REFUSED}{made}: {err}. Approve again to carry on") from err
    repo_of = {issue["ref"]: issue["repo"] for issue in plan.get("issues") or []}

    def settle() -> None:
        with db.connect(autocommit=True) as conn:
            plans.set_tracked(conn, goal["id"], plan["version"], tracked)
            for item in plan.get("items") or []:
                ref, repo = item["ref"], repo_of.get(item["ref"]) or goal["repos"][0]
                if item["decision"] == plans.LEAVE_OUT:
                    store.plan_leave_out(conn, goal["id"], ref, repo)
                elif item["decision"] == plans.SPLIT:
                    into = [created[d] for d in item["replaced_by"] if d in created]
                    why = f"the plan split it into {len(into)} smaller task{'' if len(into) == 1 else 's'}, which run in its place"
                    store.plan_split(
                        conn, goal["id"], ref, repo, into, f"{why}: {item['reason']}" if item["reason"] else why
                    )
                else:
                    store.plan_keep(conn, goal["id"], ref, repo, [created.get(d, d) for d in item["depends_on"]])

    try:
        await asyncio.to_thread(settle)
    except store.GoalError as err:
        raise PlanError(
            f"the plan cannot be applied: {err}. Remove that issue from the plan, then approve again"
        ) from err
    # Each split issue's readers learn where its work went.
    for item in plan.get("items") or []:
        if item["decision"] == plans.SPLIT and (into := [created[d] for d in item["replaced_by"] if d in created]):
            # The issue it split is the goal's tracker's, wherever the new tasks were written.
            await splits.announce(originals or writer, item["ref"], into, goal_id=goal["id"], by=who)
    events.emit(
        "plan_approved",
        {"goal_id": goal["id"], "version": plan["version"], "issues": list(created.values())},
        actor=who,
        tags={"goal_id": goal["id"]},
    )
    return {**plan, "created": created}
