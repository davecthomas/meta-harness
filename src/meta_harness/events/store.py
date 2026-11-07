"""The event service's Postgres store: append, read, and the rows `tail` sends.

Only the event service imports this module. Producers and consumers use the
event service's HTTP interface, through `meta_harness.events.client`.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import jsonschema
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from meta_harness.events import types

NOTIFY_CHANNEL = "mh_event_committed"
COLUMNS = (
    "position, event_id, event_type, schema_version, time_utc, source, actor, run_id, task_id, "
    "session_id, request_id, seq, trace_id, config_id, config_version, tags, payload"
)
# Filters `tail` and `read` accept, each a column.
FILTER_COLUMNS = ("run_id", "task_id", "event_type", "source")


class RejectedEvent(ValueError):
    """An event the schema registry does not accept. The producer has a bug; retrying cannot help."""


@dataclass
class NewEvent:
    event_id: uuid.UUID
    event_type: str
    schema_version: int
    time_utc: datetime
    source: str
    actor: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    request_id: str | None = None
    seq: int | None = None
    trace_id: str | None = None
    config_id: str | None = None
    config_version: str | None = None
    tags: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)


def sync_registry(conn: psycopg.Connection) -> None:
    """Write the code's registry to `event_type`. A changed schema under an existing version is refused."""
    with conn.transaction():
        for event_type in types.REGISTRY:
            row = conn.execute(
                "SELECT json_schema FROM events.event_type WHERE name = %s AND schema_version = %s",
                (event_type.name, event_type.schema_version),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO events.event_type (name, schema_version, json_schema, retention_class) "
                    "VALUES (%s, %s, %s, %s)",
                    (
                        event_type.name,
                        event_type.schema_version,
                        Jsonb(event_type.json_schema),
                        event_type.retention_class,
                    ),
                )
            elif row[0] != event_type.json_schema:
                raise RuntimeError(
                    f"event type {event_type.name} v{event_type.schema_version} changed its schema; give it a new version"
                )


def validate(event: NewEvent) -> None:
    event_type = types.by_key().get((event.event_type, event.schema_version))
    if event_type is None:
        raise RejectedEvent(f"unknown event type {event.event_type} v{event.schema_version}")
    try:
        jsonschema.validate(event.payload, event_type.json_schema)
    except jsonschema.ValidationError as err:
        raise RejectedEvent(f"{event.event_type} v{event.schema_version}: {err.message}") from err


def append(conn: psycopg.Connection, events: list[NewEvent]) -> list[int]:
    """Validate and insert a batch in one transaction, then notify. Idempotent on event_id.

    Returns each event's position, the stored one for an event_id seen before.
    The whole batch is refused when any event is invalid.
    """
    for event in events:
        validate(event)
    positions: list[int] = []
    with conn.transaction():
        for event in events:
            row = conn.execute(
                "INSERT INTO events.event (event_id, event_type, schema_version, time_utc, source, actor, run_id, "
                "task_id, session_id, request_id, seq, trace_id, config_id, config_version, tags, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (event_id) DO NOTHING RETURNING position",
                (
                    event.event_id,
                    event.event_type,
                    event.schema_version,
                    event.time_utc,
                    event.source,
                    event.actor,
                    event.run_id,
                    event.task_id,
                    event.session_id,
                    event.request_id,
                    event.seq,
                    event.trace_id,
                    event.config_id,
                    event.config_version,
                    Jsonb(event.tags),
                    Jsonb(event.payload),
                ),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT position FROM events.event WHERE event_id = %s", (event.event_id,)
                ).fetchone()
            positions.append(row[0])
        if positions:
            # Delivered when the transaction commits, so a listener never sees an uncommitted position.
            conn.execute(
                "SELECT pg_notify(%s, %s)",
                (NOTIFY_CHANNEL, json.dumps({"position": max(positions)})),
            )
    return positions


def _where(filters: dict[str, str]) -> tuple[str, list[Any]]:
    """A filter value with commas matches any of its parts, so one tail can follow many tasks."""
    clauses, params = [], []
    for column, value in filters.items():
        if column not in FILTER_COLUMNS:
            raise ValueError(f"unknown filter {column}")
        values = [v for v in value.split(",") if v]
        if len(values) > 1:
            clauses.append(f"{column} = ANY(%s)")
            params.append(values)
        else:
            clauses.append(f"{column} = %s")
            params.append(value)
    return ("".join(f" AND {c}" for c in clauses), params)


def read(
    conn: psycopg.Connection,
    filters: dict[str, str],
    from_position: int,
    limit: int,
    before_position: int | None = None,
) -> list[dict[str, Any]]:
    """Events that match the filters, in position order: the first `limit` after from_position, or with
    before_position the last `limit` before it, for paging back through a run's history."""
    where, params = _where(filters)
    with conn.cursor(row_factory=dict_row) as cur:
        if before_position is not None:
            cur.execute(
                f"SELECT {COLUMNS} FROM events.event WHERE position < %s AND position > %s{where} "
                f"ORDER BY position DESC LIMIT %s",
                [before_position, from_position, *params, limit],
            )
            return [_jsonable(row) for row in reversed(cur.fetchall())]
        cur.execute(
            f"SELECT {COLUMNS} FROM events.event WHERE position > %s{where} ORDER BY position LIMIT %s",
            [from_position, *params, limit],
        )
        return [_jsonable(row) for row in cur.fetchall()]


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    row["event_id"] = str(row["event_id"])
    row["time_utc"] = row["time_utc"].isoformat()
    return row
