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
    EventType("goal_changed", 1, _object({"goal_id": {"type": "string"}, "changes": {"type": "object"}}, ["goal_id"])),
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
    EventType("task_unblocked", 1, _object({"ref": {"type": "string"}}, ["ref"])),
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
        "compacted",
        "turn_finished",
        "run_finished",
    }
)


def by_key() -> dict[tuple[str, int], EventType]:
    return {(t.name, t.schema_version): t for t in REGISTRY}
