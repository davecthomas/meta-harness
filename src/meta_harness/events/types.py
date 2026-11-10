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
    EventType(
        "pull_request_opened",
        1,
        _object(
            {"pr_url": {"type": "string"}, "branch": {"type": "string"}, "files": {"type": "array"}},
            ["pr_url", "branch"],
        ),
    ),
    # The review loop on a task's pull request
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
    EventType("goal_met", 1, _object({"goal_id": {"type": "string"}, "reason": {"type": "string"}}, ["goal_id"])),
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
        "operator_message_delivered",
        "compacted",
        "turn_finished",
        "run_finished",
    }
)


def by_key() -> dict[tuple[str, int], EventType]:
    return {(t.name, t.schema_version): t for t in REGISTRY}
