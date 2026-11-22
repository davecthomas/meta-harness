import socket
import threading
import time
import uuid

import pytest
import uvicorn
from fastapi.testclient import TestClient

from meta_harness import db, migrations
from meta_harness.events import store
from meta_harness.events.client import EventClient, new_event
from meta_harness.events.service import create_app

pytestmark = pytest.mark.db


@pytest.fixture(scope="module", autouse=True)
def migrated():
    try:
        with db.connect() as conn:
            migrations.apply(conn)
    except Exception as err:  # noqa: BLE001
        pytest.skip(f"no Postgres at {db.database_url()}: {err}")


KEYS = {"test-service-key": "test"}
AUTH = {"Authorization": "Bearer test-service-key"}


@pytest.fixture(autouse=True)
def _service_key(monkeypatch) -> None:
    """The event client sends the service key from its environment."""
    monkeypatch.setenv("MH_SERVICE_KEY", "test-service-key")


def test_every_route_but_health_takes_a_service_key(monkeypatch):
    """Events hold run prompts, and an appended event is taken as fact by the audit record and a goal's spend."""
    with TestClient(create_app(service_keys=KEYS)) as http:
        assert http.get("/health").status_code == 200
        wrong = {"Authorization": "Bearer not-a-key"}
        for headers in ({}, wrong):
            assert http.get("/v1/events", headers=headers).status_code == 401
            assert http.post("/v1/events", json={"events": []}, headers=headers).status_code == 401
            assert http.get("/v1/events/tail", headers=headers).status_code == 401
    monkeypatch.delenv("MH_SERVICE_KEYS", raising=False)
    with pytest.raises(RuntimeError, match="MH_SERVICE_KEYS is not set"):
        create_app()


@pytest.fixture
def run_id() -> str:
    return f"test-run-{uuid.uuid4().hex[:8]}"


def _ping(run_id: str, n: int, **fields) -> dict:
    return new_event("debug_ping", "test", {"n": n}, run_id=run_id, **fields)


def test_append_is_idempotent_on_event_id(run_id):
    with TestClient(create_app(service_keys=KEYS), headers=AUTH) as http:
        event = _ping(run_id, 1)
        first = http.post("/v1/events", json={"events": [event]}).json()["positions"]
        again = http.post("/v1/events", json={"events": [event]}).json()["positions"]
        assert first == again
        stored = http.get("/v1/events", params={"run_id": run_id}).json()["events"]
        assert len(stored) == 1


def test_an_unknown_type_or_bad_payload_is_rejected_whole(run_id):
    with TestClient(create_app(service_keys=KEYS), headers=AUTH) as http:
        unknown = new_event("no_such_type", "test", {}, run_id=run_id)
        bad = new_event("debug_ping", "test", {"n": "one"}, run_id=run_id)
        assert http.post("/v1/events", json={"events": [_ping(run_id, 1), unknown]}).status_code == 422
        assert http.post("/v1/events", json={"events": [bad]}).status_code == 422
        assert http.get("/v1/events", params={"run_id": run_id}).json()["events"] == []


def test_read_filters_and_pages_in_position_order(run_id):
    with TestClient(create_app(service_keys=KEYS), headers=AUTH) as http:
        http.post("/v1/events", json={"events": [_ping(run_id, n) for n in range(1, 6)]})
        http.post("/v1/events", json={"events": [_ping("someone-else", 99)]})
        page = http.get("/v1/events", params={"run_id": run_id, "limit": 3}).json()
        assert [e["payload"]["n"] for e in page["events"]] == [1, 2, 3]
        rest = http.get("/v1/events", params={"run_id": run_id, "from_position": page["next_position"]}).json()
        assert [e["payload"]["n"] for e in rest["events"]] == [4, 5]


def test_the_outbox_drops_only_the_rejected_event(run_id):
    with _Server() as server:
        client = EventClient(server.url, source="test")
        client.emit("debug_ping", {"n": 1}, run_id=run_id)
        client.emit("debug_ping", {"n": "bad"}, run_id=run_id)
        client.emit("debug_ping", {"n": 3}, run_id=run_id)
        assert client.flush(10)
        stored = client.read(run_id=run_id)["events"]
        client.close()
        assert [e["payload"]["n"] for e in stored] == [1, 3]
        assert client.rejected == 1


def test_a_late_commit_reaches_the_tail(run_id):
    """A row that commits after a higher position is re-read from the window, not lost."""
    with _Server() as server:
        received: list[int] = []
        _tail_into(received, server.url, run_id)
        with db.connect() as early, db.connect() as late:
            early_tx = early.transaction()
            early_tx.__enter__()
            store.append(early, [store.NewEvent(**_parsed(_ping(run_id, 1)))])
            store.append(late, [store.NewEvent(**_parsed(_ping(run_id, 2)))])
            late.commit()
            _wait_for(lambda: received == [2])
            early_tx.__exit__(None, None, None)
            early.commit()
        _wait_for(lambda: sorted(received) == [1, 2])


def test_tail_keeps_going_across_an_event_service_restart(run_id):
    port = _free_port()
    received: list[int] = []
    with _Server(port) as server:
        _tail_into(received, server.url, run_id)
        producer = EventClient(server.url, source="test")
        for n in range(1, 4):
            producer.emit("debug_ping", {"n": n}, run_id=run_id)
        assert producer.flush(10)
        _wait_for(lambda: received == [1, 2, 3])
    # The event service is down; the outbox holds these until it is back.
    for n in range(4, 7):
        producer.emit("debug_ping", {"n": n}, run_id=run_id)
    time.sleep(1)
    with _Server(port):
        assert producer.flush(30)
        _wait_for(lambda: received == [1, 2, 3, 4, 5, 6], timeout=30)
    producer.close()


# Helpers


class _Server:
    """The event service on a real port, in a thread, so a test can stop and restart it."""

    def __init__(self, port: int | None = None) -> None:
        self.port = port or _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(
            uvicorn.Config(create_app(service_keys=KEYS), host="127.0.0.1", port=self.port, log_level="warning")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        _wait_for(lambda: self.server.started, timeout=10)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.server.force_exit = True
        self.thread.join(10)


def _tail_into(received: list[int], url: str, run_id: str) -> None:
    def follow():
        for event in EventClient(url).tail(run_id=run_id):
            received.append(event["payload"]["n"])

    threading.Thread(target=follow, daemon=True).start()
    time.sleep(0.5)


def _parsed(event: dict) -> dict:
    from datetime import datetime

    return {**event, "event_id": uuid.UUID(event["event_id"]), "time_utc": datetime.fromisoformat(event["time_utc"])}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(condition, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


def test_a_refused_batch_is_dropped_with_what_to_do_and_later_events_still_go(monkeypatch, caplog):
    """A wrong service key made the outbox send one batch forever, and log that the service was unreachable."""
    import httpx

    from meta_harness.events import client as events_client

    asked: list[int] = []
    answers = [401, 200]

    def handle(request: httpx.Request) -> httpx.Response:
        status = answers[0] if len(asked) == 0 else answers[-1]
        asked.append(status)
        return httpx.Response(status, json={"detail": "unknown API key"} if status == 401 else {"positions": [1]})

    made = EventClient("http://events.test", source="test")
    made.http = httpx.Client(base_url="http://events.test", transport=httpx.MockTransport(handle))

    with caplog.at_level("ERROR", logger=events_client.log.name):
        made.emit("debug_ping", {"n": 1})
        assert made.flush(5)
        made.emit("debug_ping", {"n": 2})
        assert made.flush(5)
    made.close()

    assert asked == [401, 200], "the refused batch is sent once, and the next one is not held behind it"
    assert made.rejected == 1
    [line] = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert "with 401" in line and "MH_SERVICE_KEY" in line and "restart it" in line


def test_a_service_with_no_key_to_send_stops_at_startup_by_name(monkeypatch):
    from meta_harness.events.client import check_service_key

    monkeypatch.setenv("MH_SERVICE_KEYS", "svc:a-long-service-key")
    monkeypatch.delenv("MH_SERVICE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MH_SERVICE_KEY is not set while MH_SERVICE_KEYS is"):
        check_service_key()

    monkeypatch.setenv("MH_SERVICE_KEY", "a-long-service-key")
    check_service_key()
    # A local stack that sets no keys at all takes calls without one.
    monkeypatch.delenv("MH_SERVICE_KEYS")
    monkeypatch.delenv("MH_SERVICE_KEY")
    check_service_key()
