"""The event service client library, used by every producer and consumer.

Producers call `emit`, which queues the event in an in-process outbox and
returns at once. A background thread sends the outbox in batches and retries
while the event service is down, so an event service outage never blocks a run.
A rejected event (422) is a producer bug: the outbox logs it and drops it,
never retrying. The outbox lives in memory, so events still queued when a
process dies are lost; `close` flushes it first.

Consumers call `read` for history and `tail` for a live stream that reconnects
from its last position after any disconnect.
"""

import json
import logging
import os
import queue
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx

log = logging.getLogger("meta_harness.events.client")

DEFAULT_URL = "http://127.0.0.1:8301"
BATCH_SIZE = 100
BATCH_WAIT_SECONDS = 0.2
OUTBOX_LIMIT = 10_000
MAX_BACKOFF_SECONDS = 30.0


def events_url() -> str:
    return os.environ.get("MH_EVENTS_URL", DEFAULT_URL)


# Fixed, so the same parts give the same event id in every process.
STABLE_ID_NAMESPACE = uuid.UUID("5b1f3a52-7d0c-4c3e-9a3b-6f1e2d4c8a90")


def stable_event_id(*parts: object) -> str:
    """An event id made from what the event records, so recording the same fact twice appends one event."""
    return str(uuid.uuid5(STABLE_ID_NAMESPACE, "/".join(str(p) for p in parts)))


def new_event(event_type: str, source: str, payload: dict[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
    """An event ready to append: a fresh event_id and the current time, plus any envelope fields."""
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "schema_version": fields.pop("schema_version", 1),
        "time_utc": datetime.now(UTC).isoformat(),
        "source": source,
        "payload": payload or {},
        **fields,
    }


class EventClient:
    def __init__(self, base_url: str | None = None, source: str = "unknown", timeout: float = 10.0) -> None:
        self.base_url = (base_url or events_url()).rstrip("/")
        self.source = source
        self.http = httpx.Client(base_url=self.base_url, timeout=timeout)
        self._outbox: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=OUTBOX_LIMIT)
        self._sender: threading.Thread | None = None
        self._stop = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self.dropped = 0
        self.rejected = 0

    # Producers

    def append(self, events: list[dict[str, Any]]) -> list[int]:
        """Append now and return the positions. Raises on any failure; most producers use `emit`."""
        response = self.http.post("/v1/events", json={"events": events})
        response.raise_for_status()
        return response.json()["positions"]

    def emit(self, event_type: str, payload: dict[str, Any] | None = None, **fields: Any) -> str:
        """Queue one event for sending and return its event_id. Never blocks and never raises."""
        event = new_event(event_type, fields.pop("source", self.source), payload, **fields)
        self._start_sender()
        try:
            self._idle.clear()
            self._outbox.put_nowait(event)
        except queue.Full:
            self.dropped += 1
            log.error("event outbox full (%d events); dropped %s", OUTBOX_LIMIT, event_type)
        return event["event_id"]

    def flush(self, timeout: float = 10.0) -> bool:
        """Wait until the outbox is empty. True when it emptied within the timeout."""
        if self._outbox.empty() and self._idle.is_set():
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._outbox.empty() and self._idle.wait(0.05):
                return True
        return False

    def close(self, timeout: float = 10.0) -> None:
        self.flush(timeout)
        self._stop.set()
        self.http.close()

    def _start_sender(self) -> None:
        if self._sender is None:
            self._sender = threading.Thread(target=self._send_loop, name="event-outbox", daemon=True)
            self._sender.start()

    def _send_loop(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            batch = self._next_batch()
            if not batch:
                self._idle.set()
                continue
            while not self._stop.is_set():
                try:
                    self._send(batch)
                    backoff = 0.5
                    break
                except httpx.HTTPError as err:
                    log.warning("event service unreachable (%s); retrying %d events in %.1fs", err, len(batch), backoff)
                    time.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            if self._outbox.empty():
                self._idle.set()

    def _next_batch(self) -> list[dict[str, Any]]:
        try:
            batch = [self._outbox.get(timeout=BATCH_WAIT_SECONDS)]
        except queue.Empty:
            return []
        while len(batch) < BATCH_SIZE:
            try:
                batch.append(self._outbox.get_nowait())
            except queue.Empty:
                break
        return batch

    def _send(self, batch: list[dict[str, Any]]) -> None:
        response = self.http.post("/v1/events", json={"events": batch})
        if response.status_code == 422:
            if len(batch) == 1:
                self.rejected += 1
                log.error("event service rejected %s: %s", batch[0]["event_type"], response.text)
                return
            # The service refuses the whole batch for one bad event; send each, so only that one drops.
            for event in batch:
                self._send([event])
            return
        if response.status_code >= 500:
            raise httpx.HTTPStatusError(
                f"event service {response.status_code}", request=response.request, response=response
            )
        response.raise_for_status()

    # Consumers

    def read(
        self, from_position: int = 0, limit: int = 500, before_position: int | None = None, **filters: str
    ) -> dict[str, Any]:
        """Events after from_position, or with before_position the last `limit` before it. A filter value
        may list several values separated by commas."""
        params: dict[str, Any] = {**filters, "from_position": from_position, "limit": limit}
        if before_position is not None:
            params["before_position"] = before_position
        response = self.http.get("/v1/events", params=params)
        response.raise_for_status()
        return response.json()

    def tail(self, from_position: int = 0, reconnect: bool = True, **filters: str) -> Iterator[dict[str, Any]]:
        """Every event matching the filters after from_position, then new ones as they commit.

        Reconnects after a disconnect or an event service restart, from the last position it yielded.
        """
        position = from_position
        backoff = 0.5
        while True:
            try:
                headers = {"Last-Event-ID": str(position)} if position else {}
                params = {**filters, "from_position": position}
                with httpx.stream(
                    "GET", f"{self.base_url}/v1/events/tail", params=params, headers=headers, timeout=None
                ) as response:
                    response.raise_for_status()
                    backoff = 0.5
                    for line in response.iter_lines():
                        if line.startswith("data: "):
                            event = json.loads(line[len("data: ") :])
                            position = max(position, event["position"])
                            yield event
            except httpx.HTTPError as err:
                if not reconnect:
                    raise
                log.warning("tail disconnected (%s); reconnecting from position %d", err, position)
            if not reconnect:
                return
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
