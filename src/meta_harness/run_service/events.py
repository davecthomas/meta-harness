"""The meta-harness run service's events, sent through the event service client library.

One client per worker process. Activities call `emit`, which queues the event
and returns; the client's outbox sends it. An activity whose event must be
stored before the workflow goes on calls `append`, which sends it at once and
raises on any failure, so Temporal retries the activity. Event ids come from `stable_id`
where a Temporal retry would otherwise append the same fact twice: the event
service ignores an event_id it has stored.
"""

import uuid
from functools import cache
from typing import Any

from meta_harness.events.client import EventClient, new_event

# Fixed, so the same inputs give the same event id in every process.
NAMESPACE = uuid.UUID("5b1f3a52-7d0c-4c3e-9a3b-6f1e2d4c8a90")


@cache
def client() -> EventClient:
    return EventClient(source="run")


def emit(event_type: str, payload: dict[str, Any], **fields: Any) -> None:
    client().emit(event_type, payload, **fields)


def append(event_type: str, payload: dict[str, Any], **fields: Any) -> None:
    """Append one event now, not through the outbox. Raises when the event service does not store it."""
    events_client = client()
    events_client.append([new_event(event_type, fields.pop("source", events_client.source), payload, **fields)])


def stable_id(*parts: object) -> str:
    return str(uuid.uuid5(NAMESPACE, "/".join(str(p) for p in parts)))


def close() -> None:
    if client.cache_info().currsize:
        client().close()
