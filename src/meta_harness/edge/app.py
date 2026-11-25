"""The edge: the console web app and the REST API on one origin.

- It serves the built console web app (console/dist) at /, falling back to
  index.html so the app's own routes load.
- It forwards each /v1 request to the owning service, adding the service key
  and naming the signed-in operator in X-MH-Actor, and streams responses
  through, so /v1/events/tail stays live.
- An identity outside the access list, MH_CONSOLE_OPERATORS, gets 403 on
  every /v1 path.

In the cloud the edge runs on Cloud Run, and the console's address is
a Firebase Hosting site, https://<console_site>.web.app (deploy/environment.json), which forwards every request
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
  changes something must come from one of the console's origins, MH_CONSOLE_ORIGIN: a comma-separated list,
  the address people are given first. The console is served at more than one address while it moves to a new
  one, and a change from any of them is the console's.

- The live event stream does not go through Firebase Hosting, which ends a
  request at 60 seconds. POST /auth/stream-ticket gives a signed-in console a
  ticket: read-only, for /v1/events/tail only, good for five minutes. The
  console opens the stream on the edge's own Cloud Run address,
  MH_STREAM_ORIGIN, with the ticket in the query, and Cloud Run keeps it open
  for up to an hour. That address answers the console's origin for the stream
  only (CORS), and the ticket never reaches the event service.

With MH_GOOGLE_CLIENT_ID unset (locally), MH_CONSOLE_DEV_IDENTITY names the
operator and there is no sign-in.

GET /v1/me answers who is signed in and their roles; GET /auth/config, the OAuth client id.

Who may use the console, and who is an admin, are the planner's users, which
an admin changes on the console's Users page (meta_harness.edge.directory).
MH_CONSOLE_OPERATORS and MH_CONSOLE_ADMINS are the first access list: they
decide until the planner has users, and seed them. A regular user gets 403 on configuration writes, kill
switch writes and every /v1/admin path, which the edge serves itself:
GET /v1/admin/deploys says what is deployed, and GET /v1/admin/logs reads the
VM's services' and the edge's logs from Cloud Logging (meta_harness.edge.logs).
"""

import asyncio
import logging
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import jwt
from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from meta_harness import __version__, retrying
from meta_harness.edge.directory import Directory
from meta_harness.edge.logs import EDGE, UNITS, LogsError, read_logs
from meta_harness.run_service.paths import REPO_ROOT

log = logging.getLogger("meta_harness.edge")

# Path prefix to the env var holding the owning service's URL, and its local default.
ROUTES: list[tuple[str, str, str]] = [
    ("/v1/goals", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/repos", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/labels", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/trackers", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/activity", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/pulls", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/search", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/limits", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    # The Overview's report on all goals, and the box to ask about the work.
    ("/v1/overview", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    # What needs the operator, across every goal and task: the one list every page reads (planner/needs.py).
    ("/v1/needs", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    # What the supervisor last found, for the Overview's "Needs you" list.
    ("/v1/supervisor", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    # The edge answers /v1/me itself; the planner keeps each user's settings under it.
    ("/v1/me", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/config", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    # An operator's own GitHub connection: connect, see it, disconnect (planner/github_api.py).
    ("/v1/github", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/costs", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    # The console's users, for admins: every /v1/admin path is an admin's.
    ("/v1/admin/users", "MH_PLANNER_URL", "http://127.0.0.1:8303"),
    ("/v1/tasks", "MH_TASKS_URL", "http://127.0.0.1:8302"),
    # What the work costs, per task, pull request and goal, for the Performance page.
    ("/v1/performance", "MH_TASKS_URL", "http://127.0.0.1:8302"),
    # How each reviewer's findings turned out, for the Admin menu's Reviewers page.
    ("/v1/reviewers", "MH_TASKS_URL", "http://127.0.0.1:8302"),
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


USERS_PATH = "/v1/admin/users"


def operators_from_env() -> set[str]:
    return {o.strip().lower() for o in os.environ.get("MH_CONSOLE_OPERATORS", "").split(",") if o.strip()}


def deployed_commit() -> str | None:
    """MH_COMMIT, which the release stack sets to the deployed commit, else the local checkout's HEAD."""
    if commit := os.environ.get("MH_COMMIT"):
        return commit
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def admins_from_env() -> set[str]:
    return {a.strip().lower() for a in os.environ.get("MH_CONSOLE_ADMINS", "").split(",") if a.strip()}


# Writes only an admin may make, by path prefix. Every /v1/admin path is an admin's, whatever the method.
ADMIN_WRITES = ("/v1/config", "/v1/kill-switch")


def admin_only(method: str, path: str) -> bool:
    """Whether a request needs the admin role."""
    if path == "/v1/admin" or path.startswith("/v1/admin/"):
        return True
    return method in WRITE_METHODS and any(path == p or path.startswith(p + "/") for p in ADMIN_WRITES)


# Calls only a control-plane service makes. The edge forwards with its service key, so it refuses these from a
# browser: appending events (which would forge the audit record and a goal's counted spend), and setting a
# task's status, recovery or drive.
SERVICE_ONLY_TASK_CALLS = ("status", "recover", "drive", "name")
# How soon a console is told to ask again while the edge has not yet read who may use it.
NOT_READY_RETRY_SECONDS = 2


def canonical(path: str) -> bool:
    """Whether a path has no empty, `.` or `..` segment and no backslash. The role checks read the path as
    sent, and the HTTP client collapses dot segments when it forwards, so `/v1/goals/../admin/users` would pass
    as a goals path and arrive as an admin one."""
    segments = path.split("/")[1:]
    return "\\" not in path and all(s not in ("", ".", "..") for s in segments)


def service_only(method: str, path: str) -> bool:
    """Whether a request is one only a control-plane service may make, never a browser."""
    if path == "/v1/events" or path.startswith("/v1/events/"):
        return method in WRITE_METHODS
    # Submitting a task names its repo, which the run service clones and pushes to. Only the planner does it.
    if path == "/v1/tasks":
        return method in WRITE_METHODS
    # The tasks still to be named, which only the planner's naming reads.
    if path == "/v1/tasks/unnamed":
        return True
    parts = path.split("/")
    return len(parts) == 5 and parts[1:3] == ["v1", "tasks"] and parts[4] in SERVICE_ONLY_TASK_CALLS


def target_for(path: str) -> str | None:
    for prefix, env, default in ROUTES:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?"):
            return os.environ.get(env, default).rstrip("/")
    return None


GOOGLE_KEYS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
SESSION_COOKIE = "__session"
# 36 hours: a sign-in in the morning lasts through the next day's work. Length costs little, since every request
# checks the access list again, so an operator taken off it is refused at once, signed in or not.
SESSION_SECONDS = 36 * 3600
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
    admins: set[str] | None = None,
    logs_transport: httpx.AsyncBaseTransport | None = None,
    directory: Directory | None = None,
) -> FastAPI:
    app_dir = dist or REPO_ROOT / "console" / "dist"
    allowed = operators if operators is not None else operators_from_env()
    admin_list = admins if admins is not None else admins_from_env()
    strangers = sorted(admin_list - allowed) if allowed else []
    if strangers:
        raise RuntimeError(f"MH_CONSOLE_ADMINS names {', '.join(strangers)}, who MH_CONSOLE_OPERATORS does not")

    local_identity = (identity if identity is not None else os.environ.get("MH_CONSOLE_DEV_IDENTITY", "")).lower()
    key = service_key if service_key is not None else os.environ.get("MH_SERVICE_KEY", "")
    client_id = google_client_id if google_client_id is not None else os.environ.get("MH_GOOGLE_CLIENT_ID", "")
    signing_key = session_key if session_key is not None else os.environ.get("MH_SESSION_KEY", "")
    origins = tuple(
        part.strip().rstrip("/")
        for part in (console_origin if console_origin is not None else os.environ.get("MH_CONSOLE_ORIGIN", "")).split(
            ","
        )
        if part.strip()
    )
    bad = [o for o in origins if not o.startswith("https://") and not o.startswith("http://localhost")]
    if bad:
        raise RuntimeError(
            f"MH_CONSOLE_ORIGIN holds {', '.join(bad)}, which is not an https address. Set it to the console's "
            "addresses, comma-separated, such as https://<console_site>.web.app"
        )
    # The address people are given: named in a refusal, so the operator knows where to go.
    origin = origins[0] if origins else ""
    stream_base = (stream_origin if stream_origin is not None else os.environ.get("MH_STREAM_ORIGIN", "")).rstrip("/")
    if client_id and not signing_key:
        raise RuntimeError("MH_GOOGLE_CLIENT_ID is set without MH_SESSION_KEY: sessions could not be signed")
    if client_id and not origin:
        raise RuntimeError(
            "MH_GOOGLE_CLIENT_ID is set without MH_CONSOLE_ORIGIN: the console could not tell its own changes from another "
            "site's. Set MH_CONSOLE_ORIGIN to the console's address, such as https://<console_site>.web.app"
        )
    verify = verify_google or (google_verifier(client_id) if client_id else None)
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None), transport=transport)
    # The planner's users decide who may sign in and who is an admin. With a stand-in transport (a test) and no
    # directory given, there is no planner to ask and the first access list decides.
    users = directory or Directory(
        None if transport is not None else os.environ.get("MH_PLANNER_URL", "http://127.0.0.1:8303"),
        key,
        allowed,
        admin_list,
    )
    is_admin = users.is_admin
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
        if not users.ready():
            return who, not_ready()
        if not users.allowed(who):
            return who, private(JSONResponse({"detail": f"{who} is not on the console's access list"}, 403))
        return who, None

    def not_ready() -> Response:
        """The edge has not read the console's users yet, as on a fresh instance while the planner restarts.
        It lets nobody in on a guess: the answer says to try again, and the console does."""
        return private(
            JSONResponse(
                {
                    "detail": "the console's users could not be read yet, so nobody is let in for now. "
                    "Try again in a few seconds; if it lasts, the planner is down: see Admin, Logs"
                },
                status_code=503,
                headers={"Retry-After": str(NOT_READY_RETRY_SECONDS)},
            )
        )

    def private(response: Response) -> Response:
        # Firebase Hosting caches what a response lets it; nothing signed-in may be cached.
        response.headers["Cache-Control"] = "no-store"
        return response

    def foreign(request: Request) -> JSONResponse | None:
        """A change from a page that is not the console's: refused, whatever cookie it carries."""
        if not origin or request.method not in WRITE_METHODS:
            return None
        sent = request.headers.get("origin", "").rstrip("/")
        # A browser names the page a change comes from, in Origin or, for a same-origin request some send
        # without it, in Sec-Fetch-Site. A change that names neither is not known to be the console's.
        same = sent in origins or (not sent and request.headers.get("sec-fetch-site") == "same-origin")
        if not same:
            detail = f"changes come only from {origin}: open the console there and make the change in it"
            return private(JSONResponse({"detail": detail}, status_code=403))
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
        if not users.ready():
            return not_ready()
        if not users.allowed(who):
            return private(JSONResponse({"detail": f"{who} is not on the console's access list"}, status_code=403))
        users.signed_in(who)
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
        if denied:
            return denied
        roles = ["operator", "admin"] if is_admin(who) else ["operator"]
        return private(JSONResponse({"identity": who, "roles": roles}))

    @app.get("/v1/version")
    def version(request: Request) -> Response:
        """What runs: the version every shipping pull request bumps, and the commit, which tells apart deployments
        of one version. MH_COMMIT is the deployed commit; locally, the checkout's."""
        _, denied = signed_in(request)
        if denied:
            return denied
        return private(JSONResponse({"version": __version__, "commit": deployed_commit()}))

    @app.get("/v1/admin/logs")
    async def admin_logs(
        request: Request,
        unit: str | None = None,
        severity: str | None = None,
        minutes: int = Query(60, ge=1, le=7 * 24 * 60),
        q: str | None = None,
    ) -> Response:
        """The newest log entries of one VM unit, or the edge, or all of them, for the Logs page."""
        who, denied = await asyncio.to_thread(signed_in, request)
        if denied:
            return denied
        if not is_admin(who):
            return private(JSONResponse({"detail": f"{who} is not an admin"}, status_code=403))
        try:
            rows = await read_logs(unit, severity, minutes, q, transport=logs_transport)
        except LogsError as err:
            return private(JSONResponse({"detail": str(err)}, status_code=502))
        return private(JSONResponse({"entries": rows, "units": [*UNITS, EDGE]}))

    @app.get("/v1/admin/deploys")
    def deploys(request: Request) -> Response:
        """What is deployed: the commit the release stack deployed to the VM and the edge, and the edge's revision."""
        who, denied = signed_in(request)
        if denied:
            return denied
        if not is_admin(who):
            return private(JSONResponse({"detail": f"{who} is not an admin"}, status_code=403))
        return private(
            JSONResponse(
                {
                    "version": __version__,
                    "commit": deployed_commit(),
                    "revision": os.environ.get("K_REVISION") or None,
                    "project": os.environ.get("MH_PROJECT") or None,
                    "region": os.environ.get("MH_REGION") or None,
                    "zone": os.environ.get("MH_ZONE") or None,
                }
            )
        )

    @app.api_route("/v1/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def forward(request: Request, rest: str) -> Response:
        # Off the event loop: the check may read the users from the planner.
        who, denied = await asyncio.to_thread(signed_in, request)
        if denied:
            return denied
        refused = foreign(request)
        if refused:
            return refused
        if not canonical(request.url.path):
            return private(
                JSONResponse(
                    {"detail": "the path has an empty, '.' or '..' part; send the path of the page or call itself"},
                    status_code=400,
                )
            )
        if service_only(request.method, request.url.path):
            return private(
                JSONResponse(
                    {
                        "detail": "only a control-plane service may make this call; do this from the console's own pages instead"
                    },
                    status_code=403,
                )
            )
        if admin_only(request.method, request.url.path) and not is_admin(who):
            return private(JSONResponse({"detail": f"{who} is not an admin"}, status_code=403))
        if request.url.path.startswith(USERS_PATH):
            # The seed and the sign-in report are the edge's own calls, never a browser's.
            if request.url.path.endswith(("/seed", "/signed-in")):
                return private(JSONResponse({"detail": "not a console path"}, status_code=404))
            if request.method in WRITE_METHODS:
                users.changed()
        base = target_for(request.url.path)
        if base is None:
            return JSONResponse({"detail": f"no service owns {request.url.path}"}, status_code=404)
        headers = {k: v for k, v in request.headers.items() if k.lower() in PASSED_HEADERS}
        # The ticket is the edge's; the event service never sees it.
        params = [(k, v) for k, v in request.query_params.multi_items() if k != "ticket"]
        headers["Authorization"] = f"Bearer {key}"
        headers["X-MH-Actor"] = who
        # The role too, for a service that answers an admin and a regular user differently (costs).
        headers["X-MH-Role"] = "admin" if is_admin(who) else "user"
        content = await request.body()
        waited, pause = 0.0, 0.25
        while True:
            upstream = client.build_request(
                request.method, f"{base}{request.url.path}", params=params, headers=headers, content=content
            )
            try:
                response = await client.send(upstream, stream=True)
                break
            except httpx.TransportError as err:
                # A request that never arrived can always go again; one that may have, only if that is harmless.
                never_arrived = isinstance(err, retrying.NOT_SENT)
                why = f"{type(err).__name__}: {err}" if str(err) else type(err).__name__
                if not never_arrived and request.method not in retrying.SAFE_METHODS:
                    log.error("%s %s failed and is not safe to repeat: %s", request.method, request.url.path, why)
                    return JSONResponse(
                        {"detail": f"{base} did not finish answering ({why}): check whether it took effect"},
                        status_code=502,
                    )
                if waited >= restart_wait_seconds:
                    log.error("%s %s gave up after %.1fs: %s", request.method, request.url.path, waited, why)
                    return private(
                        JSONResponse(
                            {"detail": f"Muxxy is updating and {base} is not back yet: {why}"},
                            status_code=503,
                            headers={"Retry-After": "5"},
                        )
                    )
                log.warning("%s %s: %s; trying again in %.2fs", request.method, request.url.path, why, pause)
                await asyncio.sleep(pause)
                waited += pause
                pause = min(pause * 2, 2.0)
            except httpx.HTTPError as err:
                return JSONResponse({"detail": f"{base} is unreachable: {err}"}, status_code=502)
        passed = {k: v for k, v in response.headers.items() if k.lower() not in DROPPED_RESPONSE_HEADERS}
        passed["Cache-Control"] = "no-store"
        # The console opens the stream on this address from its own origin: let it read the stream, only.
        asking = request.headers.get("origin", "").rstrip("/")
        if origins and request.url.path == STREAM_PATH and asking in origins:
            passed["Access-Control-Allow-Origin"] = asking
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
