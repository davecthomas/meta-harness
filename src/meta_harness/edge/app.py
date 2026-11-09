"""The edge: the console web app and the REST API on one origin.

- It serves the built console web app (console/dist) at /, falling back to
  index.html so the app's own routes load.
- It forwards each /v1 request to the owning service, adding the service key
  and naming the signed-in operator in X-MH-Actor, and streams responses
  through, so /v1/events/tail stays live.
- An identity outside the access list, MH_CONSOLE_OPERATORS, gets 403 on
  every /v1 path.

In the cloud the edge runs on Cloud Run, and the console's address is
Firebase Hosting's https://meta-harness.web.app, which forwards every request
here. The edge signs operators in itself, with Google:

- The console shows Google's sign-in button (Google Identity Services). Its ID
  token goes to POST /auth/google, which verifies it (Google's signature, the
  console's OAuth client as audience, a verified email) and checks the access
  list. It then sets a session: a token signed with MH_SESSION_KEY, in the
  cookie `__session`, the only cookie Firebase Hosting passes to Cloud Run.
- Every /v1 request needs a valid session. Without one it gets 401, at any
  address, so the Cloud Run URL offers nothing without signing in, and only
  the console's origin can sign in (the OAuth client lists only it).
- The cookie is HttpOnly, Secure and SameSite=Strict, and a request that
  changes something must come from the console's origin, MH_CONSOLE_ORIGIN.

- The live event stream does not go through Firebase Hosting, which ends a
  request at 60 seconds. POST /auth/stream-ticket gives a signed-in console a
  ticket: read-only, for /v1/events/tail only, good for five minutes. The
  console opens the stream on the edge's own Cloud Run address,
  MH_STREAM_ORIGIN, with the ticket in the query, and Cloud Run keeps it open
  for up to an hour. That address answers the console's origin for the stream
  only (CORS), and the ticket never reaches the event service.

With MH_GOOGLE_CLIENT_ID unset (locally), MH_CONSOLE_DEV_IDENTITY names the
operator and there is no sign-in.

GET /v1/me answers who is signed in; GET /auth/config, the OAuth client id.
"""

import asyncio
import os
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import jwt
from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from meta_harness.run_service.paths import REPO_ROOT

# Path prefix to the env var holding the owning service's URL, and its local default.
ROUTES: list[tuple[str, str, str]] = [
    ("/v1/goals", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/repos", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/labels", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/activity", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/config", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/tasks", "MH_TASKS_URL", "http://127.0.0.1:8302"),
    ("/v1/events", "MH_EVENTS_URL", "http://127.0.0.1:8301"),
    ("/v1/kill-switch", "MH_SCHEDULER_URL", "http://127.0.0.1:8304"),
]
# A deploy restarts each service for a few seconds. A request that cannot connect is tried again for this
# long: a refused connection means the service never saw it, so even a change is safe to send again.
RESTART_WAIT_SECONDS = 20.0
# Request headers the edge passes on; it sets Authorization and X-MH-Actor itself.
PASSED_HEADERS = {"content-type", "accept", "last-event-id"}
# cache-control: the edge sets its own (no-store), so an upstream's cannot let Firebase Hosting cache.
DROPPED_RESPONSE_HEADERS = {"content-length", "transfer-encoding", "connection", "content-encoding", "cache-control"}


def operators_from_env() -> set[str]:
    return {o.strip().lower() for o in os.environ.get("MH_CONSOLE_OPERATORS", "").split(",") if o.strip()}


def target_for(path: str) -> str | None:
    for prefix, env, default in ROUTES:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?"):
            return os.environ.get(env, default).rstrip("/")
    return None


GOOGLE_KEYS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
SESSION_COOKIE = "__session"
SESSION_SECONDS = 12 * 3600
# A stream ticket opens the event stream; long enough to cover a reconnect after a dropped connection.
TICKET_SECONDS = 300
STREAM_PATH = "/v1/events/tail"
# Changes, which must come from the console's origin.
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class SignInError(ValueError):
    pass


def google_verifier(client_id: str) -> Callable[[str], str]:
    """A function from a Google ID token to its verified email.

    The token is the RS256 JWT that Google Identity Services hands the console after sign-in. Its
    audience is the console's OAuth client, and its email must be one Google verified.
    """
    keys = jwt.PyJWKClient(GOOGLE_KEYS_URL, cache_keys=True)

    def verify(token: str) -> str:
        if not token:
            raise SignInError("no Google ID token")
        try:
            claims = jwt.decode(
                token,
                keys.get_signing_key_from_jwt(token).key,
                algorithms=["RS256"],
                audience=client_id,
                issuer=GOOGLE_ISSUERS,
            )
        except jwt.PyJWTError as err:
            raise SignInError(str(err)) from err
        if not claims.get("email_verified"):
            raise SignInError("Google has not verified this account's email")
        return str(claims.get("email", "")).lower()

    return verify


def new_session(who: str, key: str, now: float | None = None) -> str:
    issued = int(now if now is not None else time.time())
    return jwt.encode({"sub": who, "iat": issued, "exp": issued + SESSION_SECONDS}, key, algorithm="HS256")


def session_identity(token: str, key: str, scope: str = "session") -> str:
    """The operator a session (or, with scope "stream", a stream ticket) names, or "" when it is missing,
    forged, expired, or of the other scope."""
    if not token:
        return ""
    try:
        claims = jwt.decode(token, key, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return ""
    return str(claims["sub"]) if claims.get("scope", "session") == scope else ""


def new_ticket(who: str, key: str, now: float | None = None) -> str:
    issued = int(now if now is not None else time.time())
    claims = {"sub": who, "scope": "stream", "iat": issued, "exp": issued + TICKET_SECONDS}
    return jwt.encode(claims, key, algorithm="HS256")


def create_app(
    dist: Path | None = None,
    operators: set[str] | None = None,
    identity: str | None = None,
    service_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    restart_wait_seconds: float = RESTART_WAIT_SECONDS,
    google_client_id: str | None = None,
    session_key: str | None = None,
    console_origin: str | None = None,
    verify_google: Callable[[str], str] | None = None,
    stream_origin: str | None = None,
) -> FastAPI:
    app_dir = dist or REPO_ROOT / "console" / "dist"
    allowed = operators if operators is not None else operators_from_env()
    local_identity = (identity if identity is not None else os.environ.get("MH_CONSOLE_DEV_IDENTITY", "")).lower()
    key = service_key if service_key is not None else os.environ.get("MH_SERVICE_KEY", "")
    client_id = google_client_id if google_client_id is not None else os.environ.get("MH_GOOGLE_CLIENT_ID", "")
    signing_key = session_key if session_key is not None else os.environ.get("MH_SESSION_KEY", "")
    origin = (console_origin if console_origin is not None else os.environ.get("MH_CONSOLE_ORIGIN", "")).rstrip("/")
    stream_base = (stream_origin if stream_origin is not None else os.environ.get("MH_STREAM_ORIGIN", "")).rstrip("/")
    if client_id and not signing_key:
        raise RuntimeError("MH_GOOGLE_CLIENT_ID is set without MH_SESSION_KEY: sessions could not be signed")
    verify = verify_google or (google_verifier(client_id) if client_id else None)
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None), transport=transport)
    app = FastAPI(title="meta-harness edge", docs_url=None, redoc_url=None)

    def signed_in(request: Request) -> tuple[str, JSONResponse | None]:
        """Who is asking, and the refusal to send when they may not. With Google sign-in on, only a
        valid session counts; locally, MH_CONSOLE_DEV_IDENTITY names the operator."""
        if verify is not None:
            who = session_identity(request.cookies.get(SESSION_COOKIE, ""), signing_key)
            if not who and request.url.path == STREAM_PATH:
                who = session_identity(request.query_params.get("ticket", ""), signing_key, scope="stream")
            if not who:
                return "", private(JSONResponse({"detail": "not signed in"}, status_code=401))
        else:
            who = local_identity
            if not who:
                return "", JSONResponse({"detail": "nobody is signed in: set MH_CONSOLE_DEV_IDENTITY"}, status_code=401)
        if who not in allowed:
            return who, private(JSONResponse({"detail": f"{who} is not on the console's access list"}, 403))
        return who, None

    def private(response: Response) -> Response:
        # Firebase Hosting caches what a response lets it; nothing signed-in may be cached.
        response.headers["Cache-Control"] = "no-store"
        return response

    def foreign(request: Request) -> JSONResponse | None:
        """A change from a page that is not the console's: refused, whatever cookie it carries."""
        sent = request.headers.get("origin", "").rstrip("/")
        if origin and request.method in WRITE_METHODS and sent and sent != origin:
            return private(JSONResponse({"detail": f"changes come only from {origin}"}, status_code=403))
        return None

    @app.get("/auth/config")
    def auth_config() -> Response:
        return private(JSONResponse({"google_client_id": client_id or None}))

    @app.post("/auth/google")
    def sign_in(request: Request, credential: str = Body(embed=True)) -> Response:
        refused = foreign(request)
        if refused:
            return refused
        if verify is None:
            return private(JSONResponse({"detail": "Google sign-in is off here"}, status_code=404))
        try:
            who = verify(credential)
        except SignInError as err:
            return private(JSONResponse({"detail": f"Google sign-in failed: {err}"}, status_code=401))
        if who not in allowed:
            return private(JSONResponse({"detail": f"{who} is not on the console's access list"}, status_code=403))
        response = private(JSONResponse({"identity": who}))
        response.set_cookie(
            SESSION_COOKIE,
            new_session(who, signing_key),
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )
        return response

    @app.post("/auth/stream-ticket")
    def stream_ticket(request: Request) -> Response:
        """Where to open the live event stream, and the ticket that opens it."""
        who, denied = signed_in(request)
        if denied:
            return denied
        refused = foreign(request)
        if refused:
            return refused
        ticket = new_ticket(who, signing_key) if verify is not None else None
        return private(JSONResponse({"base": stream_base, "ticket": ticket}))

    @app.post("/auth/logout")
    def sign_out() -> Response:
        response = private(JSONResponse({"signed_out": True}))
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return response

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/v1/me")
    def me(request: Request) -> Response:
        who, denied = signed_in(request)
        return denied or private(JSONResponse({"identity": who}))

    @app.api_route("/v1/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def forward(request: Request, rest: str) -> Response:
        who, denied = signed_in(request)
        if denied:
            return denied
        refused = foreign(request)
        if refused:
            return refused
        base = target_for(request.url.path)
        if base is None:
            return JSONResponse({"detail": f"no service owns {request.url.path}"}, status_code=404)
        headers = {k: v for k, v in request.headers.items() if k.lower() in PASSED_HEADERS}
        # The ticket is the edge's; the event service never sees it.
        params = [(k, v) for k, v in request.query_params.multi_items() if k != "ticket"]
        headers["Authorization"] = f"Bearer {key}"
        headers["X-MH-Actor"] = who
        content = await request.body()
        waited, pause = 0.0, 0.25
        while True:
            upstream = client.build_request(
                request.method, f"{base}{request.url.path}", params=params, headers=headers, content=content
            )
            try:
                response = await client.send(upstream, stream=True)
                break
            except httpx.ConnectError as err:
                if waited >= restart_wait_seconds:
                    return private(
                        JSONResponse(
                            {"detail": f"the meta-harness is updating and {base} is not back yet: {err}"},
                            status_code=503,
                            headers={"Retry-After": "5"},
                        )
                    )
                await asyncio.sleep(pause)
                waited += pause
                pause = min(pause * 2, 2.0)
            except httpx.HTTPError as err:
                return JSONResponse({"detail": f"{base} is unreachable: {err}"}, status_code=502)
        passed = {k: v for k, v in response.headers.items() if k.lower() not in DROPPED_RESPONSE_HEADERS}
        passed["Cache-Control"] = "no-store"
        # The console opens the stream on this address from its own origin: let it read the stream, only.
        if origin and request.url.path == STREAM_PATH and request.headers.get("origin", "").rstrip("/") == origin:
            passed["Access-Control-Allow-Origin"] = origin
            passed["Vary"] = "Origin"

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
