"""The edge: the console web app and the REST API on one origin.

- It serves the built console web app (console/dist) at /, falling back to
  index.html so the app's own routes load.
- It forwards each /v1 request to the owning service, adding the service key
  and naming the signed-in operator in X-MH-Actor, and streams responses
  through, so /v1/events/tail stays live.
- An identity outside the access list, MH_CONSOLE_OPERATORS, gets 403 on
  every /v1 path.

In the cloud the edge runs on Cloud Run behind Identity-Aware Proxy: with
MH_EDGE_IAP_AUDIENCE set, the operator is the email in IAP's verified signed
header, and a request without a valid one gets 401. Locally there is no IAP,
and MH_CONSOLE_DEV_IDENTITY names the operator.

GET /v1/me answers who is signed in.
"""

import os
from pathlib import Path

import httpx
import jwt
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from meta_harness.run_service.paths import REPO_ROOT

# Path prefix to the env var holding the owning service's URL, and its local default.
ROUTES: list[tuple[str, str, str]] = [
    ("/v1/goals", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/repos", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/config", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
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


IAP_ASSERTION_HEADER = "x-goog-iap-jwt-assertion"
IAP_KEYS_URL = "https://www.gstatic.com/iap/verify/public_key-jwk"
IAP_ISSUER = "https://cloud.google.com/iap"


class IapError(ValueError):
    pass


def iap_verifier(audience: str):
    """A function from IAP's signed header to the verified email, or None when the edge runs without IAP.

    The header is an ES256 JWT that IAP signs with keys Google publishes. Its audience names this
    service; for Cloud Run that is /projects/<number>/locations/<region>/services/<name>.
    """
    if not audience:
        return None
    keys = jwt.PyJWKClient(IAP_KEYS_URL, cache_keys=True)

    def verify(token: str) -> str:
        if not token:
            raise IapError("the request has no IAP assertion")
        try:
            claims = jwt.decode(
                token,
                keys.get_signing_key_from_jwt(token).key,
                algorithms=["ES256"],
                audience=audience,
                issuer=IAP_ISSUER,
            )
        except jwt.PyJWTError as err:
            raise IapError(str(err)) from err
        email = str(claims.get("email", "")).lower()
        # IAP prefixes external identities with their issuer, as in "accounts.google.com:dave@example.com".
        return email.rsplit(":", 1)[-1]

    return verify


def create_app(
    dist: Path | None = None,
    operators: set[str] | None = None,
    identity: str | None = None,
    service_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    app_dir = dist or REPO_ROOT / "console" / "dist"
    allowed = operators if operators is not None else operators_from_env()
    local_identity = (identity if identity is not None else os.environ.get("MH_CONSOLE_DEV_IDENTITY", "")).lower()
    key = service_key if service_key is not None else os.environ.get("MH_SERVICE_KEY", "")
    iap = iap_verifier(os.environ.get("MH_EDGE_IAP_AUDIENCE", ""))
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None), transport=transport)
    app = FastAPI(title="meta-harness edge", docs_url=None, redoc_url=None)

    def signed_in(request: Request) -> tuple[str, JSONResponse | None]:
        """Who is asking, and the refusal to send when they may not. Behind IAP, only its verified
        signed header counts; locally, MH_CONSOLE_DEV_IDENTITY names the operator."""
        if iap is not None:
            try:
                who = iap(request.headers.get(IAP_ASSERTION_HEADER, ""))
            except IapError as err:
                return "", JSONResponse({"detail": f"not signed in through Identity-Aware Proxy: {err}"}, 401)
        else:
            who = local_identity
        if not who:
            return "", JSONResponse({"detail": "nobody is signed in: set MH_CONSOLE_DEV_IDENTITY"}, status_code=401)
        if who not in allowed:
            return who, JSONResponse({"detail": f"{who} is not on the console's access list"}, status_code=403)
        return who, None

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/v1/me")
    def me(request: Request) -> Response:
        who, denied = signed_in(request)
        return denied or JSONResponse({"identity": who})

    @app.api_route("/v1/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def forward(request: Request, rest: str) -> Response:
        who, denied = signed_in(request)
        if denied:
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
