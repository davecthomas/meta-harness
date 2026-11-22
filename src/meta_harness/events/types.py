"""The schema registry's contents: every event type a meta-harness producer may append.

A producer adds its event types here. The event service writes this registry to
its `event_type` table at startup, and validates each appended event's payload
against its type's JSON Schema. A changed payload shape gets a new
schema_version; the old version stays, so stored events keep their schema.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EventType:
    name: str
    schema_version: int
    json_schema: dict[str, Any]
    # short: per-turn text and tool output, dropped first. long: runs, routing, attempts, audit.
    retention_class: str = "long"


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": True,
    }


REGISTRY: list[EventType] = [
    # A test producer's event, for `mh events emit` and the event service checks.
    EventType("debug_ping", 1, _object({"n": {"type": "integer"}, "note": {"type": "string"}}, ["n"]), "short"),
    # Task service
    EventType(
        "task_created",
        1,
        _object(
            {
                "external_ref": {"type": "string"},
                "repo": {"type": "string"},
                "task_type": {"type": "string"},
                "status": {"type": "string"},
                "goal_id": {"type": ["string", "null"]},
                "parent_task_id": {"type": ["string", "null"]},
            },
            ["external_ref", "repo", "task_type", "status"],
        ),
    ),
    EventType(
        "task_status_changed",
        1,
        _object(
            {"from": {"type": "string"}, "to": {"type": "string"}, "reason": {"type": ["string", "null"]}},
            ["from", "to"],
        ),
    ),
    EventType("task_cancel_requested", 1, _object({"reason": {"type": "string"}}, ["reason"])),
    # Meta-harness run service: the RunTask workflow's route and run activities
    EventType(
        "task_routed",
        1,
        _object(
            {
                "route_no": {"type": "integer"},
                "tier": {"type": "string"},
                "method": {"type": "string"},
                "confidence": {"type": ["number", "null"]},
                "candidates": {"type": "array", "items": {"type": "string"}},
                "floor": {"type": ["string", "null"]},
                "ceiling": {"type": ["string", "null"]},
                "classifier_usd": {"type": "string"},
            },
            ["route_no", "tier", "method", "candidates"],
        ),
    ),
    EventType(
        "attempt_finished",
        1,
        _object(
            {
                "attempt": {"type": "integer"},
                "tier": {"type": ["string", "null"]},
                "candidate": {"type": "string"},
                "decision": {"type": "string", "enum": ["finished", "next_candidate", "final"]},
                "detail": {"type": "string"},
                "spent_usd": {"type": "string"},
                "status": {"type": ["string", "null"]},
                "end_reason": {"type": ["string", "null"]},
            },
            ["attempt", "candidate", "decision", "detail", "spent_usd"],
        ),
    ),
    # The filled-in prompt a run was given, recorded on the task before the run starts.
    EventType(
        "run_prompt",
        1,
        _object(
            {
                "run_id": {"type": "string"},
                "kind": {"type": "string", "enum": ["task", "review", "fix"]},
                "template": {"type": "string"},
                "prompt": {"type": "string"},
            },
            ["run_id", "kind", "template", "prompt"],
        ),
    ),
    # Where a run came from, as the control plane knows it (run_service/run_provenance.py), before the run starts.
    EventType(
        "run_provenance",
        1,
        _object(
            {
                "task_id": {"type": "string"},
                "run_id": {"type": "string"},
                "attempt": {"type": "integer"},
                "kind": {"type": "string", "enum": ["task", "review", "fix"]},
                "candidate": {"type": "string"},
                "template": {"type": "string"},
                "tier": {"type": ["string", "null"]},
                "follows": {"type": ["string", "null"]},
                "workflow": {"type": "integer", "minimum": 1},
                "goal_id": {"type": ["string", "null"]},
                "issue": {"type": ["string", "null"]},
                "parent_task_id": {"type": ["string", "null"]},
                "task_type": {"type": ["string", "null"]},
                "repo": {"type": ["string", "null"]},
            },
            ["task_id", "run_id", "attempt", "kind", "candidate", "template", "workflow"],
        ),
    ),
    EventType(
        "pull_request_opened",
        1,
        _object(
            {"pr_url": {"type": "string"}, "branch": {"type": "string"}, "files": {"type": "array"}},
            ["pr_url", "branch"],
        ),
    ),
    # The summary a run's pull request description holds, from the opening (fix_round 0) or a fix round, and
    # where it came from: the run's own words, or a fallback the meta-harness wrote (run_service/pr_summary.py).
    EventType(
        "pull_request_summary",
        1,
        _object(
            {
                "pr_url": {"type": "string"},
                "fix_round": {"type": "integer"},
                "source": {"type": "string", "enum": ["run", "recorded", "files"]},
                "heading": {"type": "string"},
                "text": {"type": "string"},
            },
            ["pr_url", "fix_round", "source"],
        ),
    ),
    # The review loop on a task's pull request
    # An operator sent a task whose workflow had ended back to the queue: a retry, or a merge they asked for
    EventType(
        "task_retried",
        1,
        _object(
            {
                "action": {"type": "string", "enum": ["retry", "merge"]},
                "resume": {"type": ["string", "null"]},
                "max_usd": {"type": ["string", "null"]},
                "retries": {"type": "integer"},
            },
            ["action", "retries"],
        ),
    ),
    # An operator started a task over: its pull request is closed and it runs fresh from the current base.
    EventType(
        "task_restarted",
        1,
        _object(
            {
                "max_usd": {"type": ["string", "null"]},
                "retries": {"type": "integer"},
                "pr_url": {"type": "string"},
            },
            ["retries"],
        ),
    ),
    # A review held for an operator before its fix run, a finding they dismissed, and their word to go on
    # An operator took a task's review over, gave it back, or gave their verdict
    EventType("operator_review_claimed", 1, _object({}, [])),
    EventType("operator_review_released", 1, _object({}, [])),
    EventType(
        "operator_review_submitted",
        1,
        _object(
            {
                "verdict": {"type": "string", "enum": ["clean", "changes"]},
                "kept": {"type": "integer"},
                "findings": {"type": "integer"},
            },
            ["verdict"],
        ),
    ),
    EventType("review_hold", 1, _object({"reason": {"type": "string"}}, ["reason"])),
    EventType(
        "finding_dismissed",
        1,
        _object(
            {"round": {"type": "integer"}, "finding": {"type": "integer"}, "reason": {"type": "string"}},
            ["round", "finding", "reason"],
        ),
    ),
    EventType("review_proceeded", 1, _object({"round": {"type": "integer"}}, ["round"])),
    # The supervisor: who has a task when that changes, and a task it sent back for want of a workflow
    EventType(
        "task_standing",
        1,
        _object(
            {
                "owner": {"enum": ["workflow", "scheduler", "planner", "supervisor", "operator"]},
                "situation": {"type": "string"},
                "reason": {"type": "string"},
                "ask": {"type": "string"},
            },
            ["owner", "situation", "reason"],
        ),
    ),
    EventType(
        "task_recovered",
        1,
        _object(
            {
                "from": {"type": "string"},
                "to": {"type": "string"},
                "recoveries": {"type": "integer"},
                "why": {"type": "string"},
            },
            ["from", "to", "recoveries", "why"],
        ),
    ),
    EventType(
        "task_driven",
        1,
        _object(
            {
                # What the supervisor sent the task back for: merge or review today. A string, not a fixed
                # list, so a new kind of drive needs no new version of the event.
                "action": {"type": "string"},
                "why": {"type": "string"},
                "drives": {"type": "integer"},
                "resume": {"type": "string"},
            },
            ["action", "why", "drives"],
        ),
    ),
    # The writing pass on the texts of one post: a pull request description, a review, a comment, an issue
    EventType(
        "writing_checked",
        1,
        _object(
            {
                "kind": {"enum": ["pull_request", "review", "comment", "issue"]},
                "texts": {"type": "integer"},
                "findings": {"type": "integer"},
                "categories": {"type": "object"},
                "rewritten": {"type": "integer"},
                "kept": {"type": "integer"},
                "reasons": {"type": "array", "items": {"type": "string"}},
                "model": {"type": "string"},
                "usd_cost": {"type": "string"},
                "changes": {
                    "type": "array",
                    "items": _object({"before": {"type": "string"}, "after": {"type": "string"}}, ["before", "after"]),
                },
            },
            ["kind", "texts", "findings", "rewritten", "kept"],
        ),
    ),
    # The files a run created that carry a provenance header (run_service/provenance_headers.py): count is how many,
    # files the first of them
    EventType(
        "provenance_added",
        1,
        _object(
            {
                "run_id": {"type": "string"},
                "count": {"type": "integer"},
                "files": {"type": "array", "items": {"type": "string"}},
            },
            ["run_id", "count", "files"],
        ),
    ),
    # A running task held for budget, and the operator's answer
    EventType("budget_hold", 1, _object({"reason": {"type": "string"}}, ["reason"])),
    EventType(
        "budget_decision",
        1,
        _object(
            {"action": {"type": "string", "enum": ["raise", "end"]}, "max_usd": {"type": ["string", "null"]}},
            ["action"],
        ),
    ),
    EventType(
        "merge_checks",
        1,
        _object(
            {
                "merge_sha": {"type": "string"},
                "state": {"type": "string", "enum": ["passed", "failed", "none", "pending"]},
                "failed": {"type": "array", "items": {"type": "string"}},
                "round": {"type": "integer"},
            },
            ["merge_sha", "state"],
        ),
    ),
    EventType(
        "pull_request_checks",
        1,
        _object(
            {
                "head_sha": {"type": "string"},
                "state": {"type": "string", "enum": ["passed", "failed", "none"]},
                "failed": {"type": "array", "items": {"type": "string"}},
            },
            ["head_sha", "state"],
        ),
    ),
    EventType(
        "pull_request_reviewed",
        1,
        _object(
            {
                "round": {"type": "integer"},
                "verdict": {"type": "string", "enum": ["clean", "changes"]},
                "reviewer": {"type": "string"},
                "head_sha": {"type": "string"},
                "findings": {"type": "integer"},
                "review_url": {"type": "string"},
            },
            ["round", "verdict", "reviewer", "head_sha"],
        ),
    ),
    EventType(
        "pull_request_merged",
        1,
        _object(
            {"pr_url": {"type": "string"}, "head_sha": {"type": "string"}, "merge_sha": {"type": "string"}},
            ["pr_url", "head_sha"],
        ),
    ),
    # A task's pull request closed without merging, as the planner's sweep found it.
    EventType(
        "pull_request_closed",
        1,
        _object({"pr_url": {"type": "string"}, "head_sha": {"type": "string"}}, ["pr_url"]),
    ),
    # Planner
    EventType(
        "goal_created",
        1,
        _object(
            {
                "goal_id": {"type": "string"},
                "statement": {"type": "string"},
                "repos": {"type": "array", "items": {"type": "string"}},
                "label": {"type": "string"},
                "modality": {"type": "string"},
                "auto_merge": {"type": "boolean"},
            },
            ["goal_id", "statement", "repos", "label", "modality"],
        ),
    ),
    EventType(
        "goal_titled",
        1,
        _object(
            {
                "goal_id": {"type": "string"},
                "title": {"type": "string"},
                "model": {"type": "string"},
                "usd_cost": {"type": "string"},
            },
            ["goal_id", "title", "model", "usd_cost"],
        ),
    ),
    # The planner wrote an update for a goal's page (planner/updates.py): one model call, and what it cost.
    EventType(
        "goal_update_written",
        1,
        _object(
            {
                "goal_id": {"type": "string"},
                "number": {"type": "integer"},
                "model": {"type": "string"},
                "usd_cost": {"type": "string"},
            },
            ["goal_id", "number", "model", "usd_cost"],
        ),
    ),
    # The planner wrote its Overview report on all running goals (planner/overview.py): the report itself, the
    # one model call and what it cost.
    EventType(
        "overview_report_written",
        1,
        _object(
            {
                "digest": {"type": "string"},
                "headline": {"type": "string"},
                "goals": {"type": "integer"},
                "model": {"type": "string"},
                "usd_cost": {"type": "string"},
                "written_at": {"type": "string"},
            },
            ["digest", "headline", "model", "usd_cost", "written_at"],
        ),
    ),
    # The planner answered the operator's question about the work from the Overview's facts (planner/overview.py):
    # the question, the answer, the one model call and what it cost.
    EventType(
        "overview_question_answered",
        1,
        _object(
            {
                "question": {"type": "string"},
                "answer": {"type": "string"},
                "in_record": {"type": "boolean"},
                "model": {"type": "string"},
                "usd_cost": {"type": "string"},
                "answered_at": {"type": "string"},
            },
            ["question", "answer", "model", "usd_cost", "answered_at"],
        ),
    ),
    EventType(
        "goal_drafted",
        1,
        _object(
            {
                "label": {"type": "string"},
                "repos": {"type": "array", "items": {"type": "string"}},
                "issues": {"type": "integer"},
                "model": {"type": "string"},
                "usd_cost": {"type": "string"},
            },
            ["label", "issues", "model"],
        ),
    ),
    EventType("goal_changed", 1, _object({"goal_id": {"type": "string"}, "changes": {"type": "object"}}, ["goal_id"])),
    # The first time a goal's spend reaches its budget: block_at is null for a goal that only warns
    EventType(
        "goal_budget_reached",
        1,
        _object(
            {
                "goal_id": {"type": "string"},
                "spent_usd": {"type": "string"},
                "max_usd": {"type": "string"},
                "mode": {"type": "string", "enum": ["block", "warn"]},
                "block_at": {"type": ["string", "null"]},
            },
            ["goal_id", "spent_usd", "max_usd", "mode"],
        ),
    ),
    EventType("goal_met", 1, _object({"goal_id": {"type": "string"}, "reason": {"type": "string"}}, ["goal_id"])),
    # The console's users: an admin invited one, changed a role, or ended one's access
    EventType(
        "user_invited",
        1,
        _object(
            {"email": {"type": "string"}, "role": {"type": "string", "enum": ["admin", "user"]}}, ["email", "role"]
        ),
    ),
    EventType(
        "user_role_changed",
        1,
        _object({"email": {"type": "string"}, "from": {"type": "string"}, "to": {"type": "string"}}, ["email", "to"]),
    ),
    EventType("user_deprovisioned", 1, _object({"email": {"type": "string"}}, ["email"])),
    # Configuration: every saved or rolled-back version, with the operator as actor
    EventType(
        "config_changed",
        1,
        _object(
            {
                "kind": {"type": "string"},
                "version": {"type": "integer"},
                "origin": {"type": "string"},
                "note": {"type": ["string", "null"]},
                "rolled_back_from": {"type": ["integer", "null"]},
            },
            ["kind", "version", "origin"],
        ),
    ),
    EventType(
        "issue_joined",
        1,
        _object(
            {"ref": {"type": "string"}, "goal_id": {"type": "string"}, "depends_on": {"type": "array"}},
            ["ref", "goal_id"],
        ),
    ),
    EventType(
        "issue_refused", 1, _object({"ref": {"type": "string"}, "reason": {"type": "string"}}, ["ref", "reason"])
    ),
    EventType("issue_closed", 1, _object({"ref": {"type": "string"}}, ["ref"])),
    # An issue sized before it became a task, with the model and its cost; and an operator's answer to one the
    # sweep held for its size or its gaps: run it anyway, or split it into the issues created
    EventType(
        "issue_sized",
        1,
        _object(
            {
                "ref": {"type": "string"},
                "goal_id": {"type": "string"},
                "size": {"type": "string", "enum": ["small", "medium", "large"]},
                "reasons": {"type": "array", "items": {"type": "string"}},
                "missing": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["acceptance_checks", "paths", "testable_outcome"]},
                },
                "split": {"type": "array", "items": {"type": "string"}},
                "model": {"type": "string"},
                "usd_cost": {"type": "string"},
            },
            ["ref", "goal_id", "size", "model", "usd_cost"],
        ),
    ),
    EventType(
        "issue_released",
        1,
        _object(
            {"ref": {"type": "string"}, "goal_id": {"type": "string"}, "held_reason": {"type": ["string", "null"]}},
            ["ref", "goal_id"],
        ),
    ),
    EventType(
        "issue_split",
        1,
        _object(
            {
                "ref": {"type": "string"},
                "goal_id": {"type": "string"},
                "issues": {"type": "array", "items": {"type": "string"}},
            },
            ["ref", "goal_id", "issues"],
        ),
    ),
    # The issue the planner filed for a merged pull request's follow-up findings (planner/follow_ups.py).
    EventType(
        "follow_ups_filed",
        1,
        _object(
            {
                "ref": {"type": "string"},
                "from_ref": {"type": "string"},
                "goal_id": {"type": "string"},
                "pr_url": {"type": "string"},
                "findings": {"type": "integer"},
            },
            ["ref", "from_ref", "goal_id", "findings"],
        ),
    ),
    EventType(
        "pull_request_commented",
        1,
        _object(
            {
                "path": {"type": "string"},
                "line": {"type": "integer"},
                "start_line": {"type": ["integer", "null"]},
                "body": {"type": "string"},
                "url": {"type": "string"},
            },
            ["path", "line", "body"],
        ),
    ),
    EventType(
        "operator_message",
        1,
        _object(
            {
                "message_id": {"type": "integer"},
                "text": {"type": "string"},
                "when": {"type": "string", "enum": ["next_turn", "after_stop", "stop"]},
            },
            ["message_id", "text", "when"],
        ),
    ),
    EventType(
        "attempt_chosen",
        1,
        _object({"message_id": {"type": "integer"}, "attempt": {"type": "integer"}}, ["message_id", "attempt"]),
    ),
    EventType(
        "issue_added",
        1,
        _object({"ref": {"type": "string"}, "goal_id": {"type": "string"}}, ["ref", "goal_id"]),
    ),
    # The planner could not assign a goal's issue to the person the goal names, or its owner
    # (planner/assignees.py): the issue, the person, why, and what the operator can do.
    EventType(
        "issue_not_assigned",
        1,
        _object(
            {
                "goal_id": {"type": "string"},
                "ref": {"type": "string"},
                "person": {"type": "string"},
                "reason": {"type": "string"},
                "todo": {"type": "string"},
            },
            ["goal_id", "ref", "person", "reason", "todo"],
        ),
    ),
    # A feature goal's plan: a planning task asked for, a plan version saved, and the plan approved
    EventType(
        "plan_requested",
        1,
        _object(
            {"goal_id": {"type": "string"}, "task_id": {"type": "string"}, "note": {"type": ["string", "null"]}},
            ["goal_id", "task_id"],
        ),
    ),
    # The planner sorted a plan's gaps for fixes it could make itself (planner/suggestions.py): one model call.
    EventType(
        "plan_fixes_assessed",
        1,
        _object(
            {
                "goal_id": {"type": "string"},
                "version": {"type": "integer"},
                "fixes": {"type": "integer"},
                "model": {"type": "string"},
                "usd_cost": {"type": "string"},
            },
            ["goal_id", "version", "fixes", "model", "usd_cost"],
        ),
    ),
    # The supervisor applied a remedy to a stuck task (supervisor/remedies): one try, with why, what it did and
    # what comes next in plain sentences. of is the most tries the remedy makes before asking; 0 for no limit.
    EventType(
        "remedy_applied",
        1,
        _object(
            {
                "situation": {"type": "string"},
                "remedy": {"type": "string"},
                "try": {"type": "integer"},
                "of": {"type": "integer"},
                "outcome": {"type": "string", "enum": ["started", "done", "failed"]},
                "because": {"type": "string"},
                "did": {"type": "string"},
                "next": {"type": "string"},
                "error": {"type": "string"},
            },
            ["situation", "remedy", "try", "of", "outcome", "because", "did", "next"],
        ),
    ),
    # The supervisor had a model read a failure its rules could not sort (supervisor/diagnose.py): one call.
    EventType(
        "failure_diagnosed",
        1,
        _object(
            {
                "goal_id": {"type": "string"},
                "kind": {"type": "string", "enum": ["transient", "permanent"]},
                "model": {"type": "string"},
                "usd_cost": {"type": "string"},
            },
            ["goal_id", "kind", "model", "usd_cost"],
        ),
    ),
    # A model drafted the parts of a held issue for an operator to edit before splitting it (planner/splits.py).
    EventType(
        "split_drafted",
        1,
        _object(
            {
                "goal_id": {"type": "string"},
                "ref": {"type": "string"},
                "parts": {"type": "integer"},
                "model": {"type": "string"},
                "usd_cost": {"type": "string"},
            },
            ["goal_id", "ref", "parts", "model", "usd_cost"],
        ),
    ),
    # The planner gave a goal's tasks their short names (planner/naming.py): one model call. goal_id is empty
    # for tasks of no goal.
    EventType(
        "task_names_written",
        1,
        _object(
            {
                "goal_id": {"type": "string"},
                "tasks": {"type": "integer"},
                "model": {"type": "string"},
                "usd_cost": {"type": "string"},
            },
            ["goal_id", "tasks", "model", "usd_cost"],
        ),
    ),
    # An operator accepted the planner's suggested fix, which became the plan's next version, or dismissed it.
    EventType(
        "plan_fix_accepted",
        1,
        _object(
            {
                "goal_id": {"type": "string"},
                "for_version": {"type": "integer"},
                "version": {"type": "integer"},
                "drafts": {"type": "integer"},
            },
            ["goal_id", "for_version", "version"],
        ),
    ),
    EventType("plan_fix_dismissed", 1, _object({"goal_id": {"type": "string"}}, ["goal_id"])),
    EventType(
        "plan_saved",
        1,
        _object(
            {
                "goal_id": {"type": "string"},
                "version": {"type": "integer"},
                "source": {"type": "string", "enum": ["planned", "edited"]},
                "drafts": {"type": "integer"},
                "invalid": {"type": "array", "items": {"type": "string"}},
            },
            ["goal_id", "version", "source", "drafts"],
        ),
    ),
    EventType(
        "plan_approved",
        1,
        _object(
            {
                "goal_id": {"type": "string"},
                "version": {"type": "integer"},
                "issues": {"type": "array", "items": {"type": "string"}},
            },
            ["goal_id", "version", "issues"],
        ),
    ),
    EventType(
        "issue_dropped",
        1,
        _object({"ref": {"type": "string"}, "goal_id": {"type": "string"}}, ["ref", "goal_id"]),
    ),
    EventType("task_unblocked", 1, _object({"ref": {"type": "string"}}, ["ref"])),
    # Meta-harness scheduler
    EventType(
        "task_admitted",
        1,
        _object({"repo": {"type": "string"}, "areas": {"type": "array", "items": {"type": "string"}}}, ["repo"]),
    ),
    EventType("task_held", 1, _object({"reason": {"type": "string"}}, ["reason"])),
    EventType(
        "kill_switch_changed",
        1,
        _object({"engaged": {"type": "boolean"}, "reason": {"type": ["string", "null"]}}, ["engaged"]),
    ),
    # The harness server Run API event stream, as the run activity's Wait step reads it. The payload is
    # the harness server event's data; seq is its event id.
    *[
        EventType(name, 1, _object({}), retention)
        for name, retention in (
            ("turn_started", "short"),
            ("model_call", "long"),
            ("tool_call", "short"),
            ("tool_result", "short"),
            ("file_changed", "short"),
            ("tests_run", "long"),
            ("guardrail_blocked", "long"),
            ("review_submitted", "long"),
            ("plan_updated", "short"),
            ("issue_drafted", "long"),
            # A planning run's decision on an issue its goal already had, and its summary of the plan.
            ("issue_planned", "long"),
            ("plan_summarized", "long"),
            ("operator_message_delivered", "long"),
            ("compacted", "short"),
            ("turn_finished", "short"),
            ("run_finished", "long"),
        )
    ],
]

# The harness server Run API event types the run activity forwards to the event service.
HARNESS_EVENT_TYPES = frozenset(
    {
        "turn_started",
        "model_call",
        "tool_call",
        "tool_result",
        "file_changed",
        "tests_run",
        "guardrail_blocked",
        "review_submitted",
        "plan_updated",
        "issue_drafted",
        "issue_planned",
        "plan_summarized",
        "operator_message_delivered",
        "compacted",
        "turn_finished",
        "run_finished",
    }
)


def by_key() -> dict[tuple[str, int], EventType]:
    return {(t.name, t.schema_version): t for t in REGISTRY}
