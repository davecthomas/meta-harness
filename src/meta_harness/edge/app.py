"""The local edge: the console web app and the REST API on one origin, as the cloud serves them.

In the cloud, the external load balancer routes each /v1 path to the service
that owns it and Identity-Aware Proxy signs the operator in. On a developer's
machine this process does both:

- It serves the built console web app (console/dist) at /, falling back to
  index.html so the app's own routes load.
- It forwards each /v1 request to the owning service, adding the service key
  and naming the signed-in operator in X-MH-Actor, and streams responses
  through, so /v1/events/tail stays live.
- The signed-in operator is MH_CONSOLE_DEV_IDENTITY. An identity outside the
  access list, MH_CONSOLE_OPERATORS, gets 403 on every /v1 path.

GET /v1/me answers who is signed in.
"""

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from meta_harness.run_service.paths import REPO_ROOT

# Path prefix to the env var holding the owning service's URL, and its local default.
ROUTES: list[tuple[str, str, str]] = [
    ("/v1/goals", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/repos", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/tasks", "MH_TASKS_URL", "http://127.0.0.1:8302"),
    ("/v1/events", "MH_EVENTS_URL", "http://127.0.0.1:8301"),
    ("/v1/kill-switch", "MH_SCHEDULER_URL", "http://127.0.0.1:8304"),
]
# Request headers the edge passes on; it sets Authorization and X-MH-Actor itself.
PASSED_HEADERS = {"content-type", "accept", "last-event-id"}
DROPPED_RESPONSE_HEADERS = {"content-length", "transfer-encoding", "connection", "content-encoding"}


def operators_from_env() -> set[str]:
    return {o.strip().lower() for o in os.environ.get("MH_CONSOLE_OPERATORS", "").split(",") if o.strip()}


def target_for(path: str) -> str | None:
    for prefix, env, default in ROUTES:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?"):
            return os.environ.get(env, default).rstrip("/")
    return None


def create_app(
    dist: Path | None = None,
    operators: set[str] | None = None,
    identity: str | None = None,
    service_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    app_dir = dist or REPO_ROOT / "console" / "dist"
    allowed = operators if operators is not None else operators_from_env()
    who = (identity if identity is not None else os.environ.get("MH_CONSOLE_DEV_IDENTITY", "")).lower()
    key = service_key if service_key is not None else os.environ.get("MH_SERVICE_KEY", "")
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None), transport=transport)
    app = FastAPI(title="meta-harness local edge", docs_url=None, redoc_url=None)

    def refused() -> JSONResponse | None:
        if not who:
            return JSONResponse({"detail": "nobody is signed in: set MH_CONSOLE_DEV_IDENTITY"}, status_code=401)
        if who not in allowed:
            return JSONResponse({"detail": f"{who} is not on the console's access list"}, status_code=403)
        return None

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/v1/me")
    def me() -> Response:
        return refused() or JSONResponse({"identity": who})

    @app.api_route("/v1/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def forward(request: Request, rest: str) -> Response:
        if denied := refused():
            return denied
        base = target_for(request.url.path)
        if base is None:
            return JSONResponse({"detail": f"no service owns {request.url.path}"}, status_code=404)
        headers = {k: v for k, v in request.headers.items() if k.lower() in PASSED_HEADERS}
        headers["Authorization"] = f"Bearer {key}"
        headers["X-MH-Actor"] = who
        upstream = client.build_request(
            request.method,
            f"{base}{request.url.path}",
            params=request.query_params,
            headers=headers,
            content=await request.body(),
        )
        try:
            response = await client.send(upstream, stream=True)
        except httpx.HTTPError as err:
            return JSONResponse({"detail": f"{base} is unreachable: {err}"}, status_code=502)
        passed = {k: v for k, v in response.headers.items() if k.lower() not in DROPPED_RESPONSE_HEADERS}

        async def body():
            # A response already read (an in-process transport) is sent whole; a live one streams.
            if response.is_stream_consumed:
                yield response.content
                return
            async for chunk in response.aiter_raw():
                yield chunk

        return StreamingResponse(
            body(),
            status_code=response.status_code,
            headers=passed,
            background=BackgroundTask(response.aclose),
        )

    @app.get("/{path:path}")
    def console(path: str) -> Response:
        asset = (app_dir / path).resolve()
        if path and asset.is_file() and app_dir.resolve() in asset.parents:
            return FileResponse(asset)
        index = app_dir / "index.html"
        if not index.is_file():
            return JSONResponse({"detail": f"the console is not built: run make console ({index} is missing)"}, 503)
        return FileResponse(index)

    return app
