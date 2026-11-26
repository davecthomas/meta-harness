"""The event service: `append`, `read` and `tail` over HTTP.

    POST /v1/events               append a batch; 422 names a rejected event, which the producer must not retry
    GET  /v1/events               read events after from_position, filtered, paged
    GET  /v1/events/tail          the same filter as a live stream of server-sent events, resumable
                                  from any position with from_position or Last-Event-ID
    GET  /health

After an append commits, Postgres NOTIFY wakes every tail handler, which fetches
the new rows. A tail handler also re-reads a window behind its cursor, because
concurrent transactions can commit out of position order, and drops repeats by
event_id. A missed notification only delays delivery: each handler also checks
every POLL_SECONDS.
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from meta_harness import auth, db
from meta_harness.events import store

POLL_SECONDS = 5.0
# How far behind its cursor a tail handler re-reads, in positions.
REREAD_WINDOW = 1000
TAIL_BATCH = 500


class EventIn(BaseModel):
    event_id: uuid.UUID
    event_type: str
    schema_version: int = 1
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
    tags: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


class AppendRequest(BaseModel):
    events: list[EventIn]


class CommitSignal:
    """Wakes tail handlers when an append commits. One LISTEN connection per event service process."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.condition = asyncio.Condition()
        self.task: asyncio.Task | None = None

    async def start(self) -> None:
        self.task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()

    async def wait(self, timeout: float) -> None:
        async with self.condition:
            try:
                await asyncio.wait_for(self.condition.wait(), timeout)
            except TimeoutError:
                pass

    async def _listen(self) -> None:
        while True:
            try:
                conn = await psycopg.AsyncConnection.connect(self.url, autocommit=True)
                async with conn:
                    await conn.execute(f"LISTEN {store.NOTIFY_CHANNEL}")
                    async for _ in conn.notifies():
                        async with self.condition:
                            self.condition.notify_all()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - reconnect; handlers keep polling meanwhile
                await asyncio.sleep(1)


def create_app(database_url: str | None = None, service_keys: dict[str, str] | None = None) -> FastAPI:
    """The event service. Every route but /health takes a control-plane service key (MH_SERVICE_KEYS): events
    hold run prompts and operator messages, and an appended event is taken as fact by the audit record and by
    a goal's counted spend, so neither is open to whatever can reach the port."""
    url = database_url or db.database_url()
    keys = service_keys if service_keys is not None else auth.keys_from_env("MH_SERVICE_KEYS")
    if not keys:
        raise RuntimeError(
            "MH_SERVICE_KEYS is not set: the event service takes no request without a service key. "
            "Set it to label:key pairs, the same value the task service has."
        )

    def service(key: str = Depends(auth.bearer)) -> str:
        if label := auth.label_for(key, keys):
            return label
        raise HTTPException(status_code=401, detail="unknown API key: send a control-plane service key")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = db.pool(url)
        with pool.connection() as conn:
            store.sync_registry(conn)
        signal = CommitSignal(url)
        await signal.start()
        app.state.pool = pool
        app.state.signal = signal
        try:
            yield
        finally:
            await signal.stop()
            pool.close()

    app = FastAPI(title="meta-harness event service", lifespan=lifespan)

    def _filters(run_id, task_id, event_type, source, goal_id=None) -> dict[str, str]:
        given = {"run_id": run_id, "task_id": task_id, "event_type": event_type, "source": source, "goal_id": goal_id}
        return {k: v for k, v in given.items() if v is not None}

    @app.get("/health")
    def health() -> dict[str, str]:
        with app.state.pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.post("/v1/events")
    def append(body: AppendRequest, who: str = Depends(service)) -> dict[str, list[int]]:
        events = [store.NewEvent(**e.model_dump()) for e in body.events]
        try:
            with app.state.pool.connection() as conn:
                return {"positions": store.append(conn, events)}
        except store.RejectedEvent as err:
            raise HTTPException(status_code=422, detail=str(err)) from err

    @app.get("/v1/events")
    def read(
        run_id: str | None = None,
        task_id: str | None = None,
        event_type: str | None = None,
        source: str | None = None,
        goal_id: str | None = None,
        from_position: int = 0,
        before_position: int | None = None,
        limit: int = Query(500, ge=1, le=5000),
        who: str = Depends(service),
    ) -> dict[str, Any]:
        """A comma-separated filter value matches any of its parts. before_position returns the last
        `limit` events before it, for paging back. goal_id: a goal's own events and its tasks'."""
        filters = _filters(run_id, task_id, event_type, source, goal_id)
        with app.state.pool.connection() as conn:
            events = store.read(conn, filters, from_position, limit, before_position)
            latest = conn.execute("SELECT coalesce(max(position), 0) FROM events.event").fetchone()[0]
        next_position = events[-1]["position"] if events else from_position
        return {"events": events, "next_position": next_position, "latest_position": latest}

    @app.get("/v1/events/tail")
    async def tail(
        request: Request,
        run_id: str | None = None,
        task_id: str | None = None,
        event_type: str | None = None,
        source: str | None = None,
        goal_id: str | None = None,
        from_position: int = 0,
        who: str = Depends(service),
    ) -> StreamingResponse:
        """goal_id: only a goal's events, or several goals' with commas. from_position below 0 starts at the
        newest event, for a page that wants what happens from now on."""
        last_event_id = request.headers.get("last-event-id")
        cursor = int(last_event_id) if last_event_id and last_event_id.isdigit() else from_position
        if cursor < 0:
            with app.state.pool.connection() as conn:
                cursor = conn.execute("SELECT coalesce(max(position), 0) FROM events.event").fetchone()[0]
        filters = _filters(run_id, task_id, event_type, source, goal_id)
        return StreamingResponse(
            _tail(app, request, filters, cursor),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return app


async def _tail(app: FastAPI, request: Request, filters: dict[str, str], cursor: int):
    """Server-sent events: each event once, in position order, then new ones as they commit."""

    def fetch(after: int, limit: int) -> list[dict[str, Any]]:
        with app.state.pool.connection() as conn:
            return store.read(conn, filters, after, limit)

    sent: dict[str, int] = {}
    floor = cursor
    yield ": tail open\n\n"
    while not await request.is_disconnected():
        # Rows that committed after rows with higher positions: inside the window, never sent.
        window_start = max(floor, cursor - REREAD_WINDOW)
        late = await run_in_threadpool(fetch, window_start, REREAD_WINDOW) if cursor > window_start else []
        late = [r for r in late if r["position"] <= cursor and r["event_id"] not in sent]
        new = await run_in_threadpool(fetch, cursor, TAIL_BATCH)
        for row in late + new:
            if row["event_id"] in sent:
                continue
            sent[row["event_id"]] = row["position"]
            cursor = max(cursor, row["position"])
            yield f"id: {row['position']}\nevent: event\ndata: {json.dumps(row)}\n\n"
        # Forget event ids that have fallen out of the re-read window.
        if len(sent) > 2 * REREAD_WINDOW:
            horizon = cursor - REREAD_WINDOW
            sent = {k: v for k, v in sent.items() if v > horizon}
        if len(new) == TAIL_BATCH:
            continue
        await app.state.signal.wait(POLL_SECONDS)
        yield ": keepalive\n\n"
