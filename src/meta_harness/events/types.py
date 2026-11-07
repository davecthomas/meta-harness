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
]


def by_key() -> dict[tuple[str, int], EventType]:
    return {(t.name, t.schema_version): t for t in REGISTRY}
