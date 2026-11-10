import httpx
from fastapi.testclient import TestClient

from meta_harness.edge.app import create_app


def _edge(tmp_path, identity="dave@example.com", operators=("dave@example.com",), admins=()):
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"path": request.url.path, "query": str(request.url.query)})

    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    app = create_app(
        dist=tmp_path,
        operators=set(operators),
        identity=identity,
        service_key="svc-key",
        transport=httpx.MockTransport(upstream),
        admins=set(admins),
    )
    return TestClient(app), seen


def test_v1_paths_go_to_their_owning_service_as_the_signed_in_operator(tmp_path):
    client, seen = _edge(tmp_path)
    assert client.get("/v1/goals?offset=25").json()["path"] == "/v1/goals"
    client.post("/v1/tasks/t-1/cancel", json={"reason": "x"})
    client.get("/v1/kill-switch")
    hosts = [r.url.port for r in seen]
    assert hosts == [8303, 8302, 8304]
    assert all(r.headers["authorization"] == "Bearer svc-key" for r in seen)
    assert all(r.headers["x-mh-actor"] == "dave@example.com" for r in seen)
    assert seen[0].url.query == b"offset=25"


def test_an_identity_outside_the_access_list_gets_403(tmp_path):
    client, seen = _edge(tmp_path, identity="someone@example.com")
    assert client.get("/v1/goals").status_code == 403
    assert client.get("/v1/me").status_code == 403
    assert seen == []


def test_me_names_the_operator_and_with_no_admin_list_every_operator_is_an_admin(tmp_path):
    client, _ = _edge(tmp_path)
    assert client.get("/v1/me").json() == {"identity": "dave@example.com", "roles": ["operator", "admin"]}


OPERATORS = ("dave@example.com", "ann@example.com")


def test_a_non_admin_cannot_change_configuration_use_the_kill_switch_or_open_admin_paths(tmp_path):
    client, seen = _edge(tmp_path, identity="ann@example.com", operators=OPERATORS, admins=("dave@example.com",))

    assert client.get("/v1/me").json()["roles"] == ["operator"]
    assert client.put("/v1/config/tiers", json={}).status_code == 403
    assert client.post("/v1/config/tiers/rollback", json={}).status_code == 403
    assert client.post("/v1/kill-switch", json={"engaged": True}).status_code == 403
    assert client.get("/v1/admin/deploys").status_code == 403
    assert seen == []
    assert client.get("/v1/config/tiers").status_code == 200, "reading stays open to every operator"
    assert client.get("/v1/kill-switch").status_code == 200
    assert client.post("/v1/tasks/t-1/cancel", json={}).status_code == 200


def test_an_admin_changes_configuration_and_reads_what_is_deployed(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_COMMIT", "4a286e3")
    monkeypatch.setenv("K_REVISION", "mh-edge-00042-abc")
    client, seen = _edge(tmp_path, identity="dave@example.com", operators=OPERATORS, admins=("dave@example.com",))

    assert client.get("/v1/me").json()["roles"] == ["operator", "admin"]
    assert client.put("/v1/config/tiers", json={}).status_code == 200
    deployed = client.get("/v1/admin/deploys").json()
    assert (deployed["commit"], deployed["revision"]) == ("4a286e3", "mh-edge-00042-abc")
    assert [r.url.path for r in seen] == ["/v1/config/tiers"]


def test_an_admin_who_is_not_an_operator_stops_the_edge_naming_the_setting(tmp_path):
    import pytest

    with pytest.raises(RuntimeError, match="MH_CONSOLE_ADMINS names eve@example.com"):
        _edge(tmp_path, operators=OPERATORS, admins=("eve@example.com",))


def test_the_app_and_its_routes_load_from_dist(tmp_path):
    client, _ = _edge(tmp_path)
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert "root" in client.get("/goals/g-1").text
    assert client.get("/../pyproject.toml").status_code in (200, 404)
    assert "root" in client.get("/../pyproject.toml").text or client.get("/../pyproject.toml").status_code == 404


# --- Google sign-in, as in the cloud -----------------------------------------------------------------

CLIENT_ID = "console-client.apps.googleusercontent.com"
ORIGIN = "https://meta-harness.web.app"
SESSION_KEY = "test-session-key-0123456789abcdef"


def _google_tokens():
    """Google's side of sign-in: an RSA key, its ID tokens, and the edge's verifier using that key."""
    import time

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    from meta_harness.edge import app as edge

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class Keys:
        def __init__(self, *args, **kwargs):
            pass

        def get_signing_key_from_jwt(self, token):
            return type("SigningKey", (), {"key": key.public_key()})()

    def token(email="dave@example.com", aud=CLIENT_ID, iss="https://accounts.google.com", verified=True, ttl=600):
        now = int(time.time())
        claims = {"email": email, "email_verified": verified, "aud": aud, "iss": iss, "iat": now, "exp": now + ttl}
        return jwt.encode(claims, key, algorithm="RS256")

    original = edge.jwt.PyJWKClient
    edge.jwt.PyJWKClient = Keys
    try:
        verify = edge.google_verifier(CLIENT_ID)
    finally:
        edge.jwt.PyJWKClient = original
    return token, verify


def _cloud_edge(tmp_path):
    token, verify = _google_tokens()
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True}, headers={"Cache-Control": "public, max-age=600"})

    (tmp_path / "index.html").write_text("<div id=root></div>")
    app = create_app(
        dist=tmp_path,
        operators={"dave@example.com"},
        identity="",
        service_key="svc-key",
        transport=httpx.MockTransport(upstream),
        google_client_id=CLIENT_ID,
        session_key=SESSION_KEY,
        console_origin=ORIGIN,
        verify_google=verify,
        stream_origin="https://edge-123.us-central1.run.app",
    )
    # TestClient talks plain HTTP; the session cookie is Secure, so the client must look like HTTPS.
    return TestClient(app, base_url="https://testserver"), token, seen


def test_without_a_session_every_v1_path_is_401_and_nothing_is_forwarded(tmp_path):
    client, _, seen = _cloud_edge(tmp_path)
    assert client.get("/v1/me").status_code == 401
    assert client.get("/v1/goals").status_code == 401
    assert client.post("/v1/goals", json={}, headers={"origin": ORIGIN}).status_code == 401
    assert seen == []


def test_signing_in_sets_a_strict_secure_session_and_forwards_as_the_operator(tmp_path):
    client, token, seen = _cloud_edge(tmp_path)
    signed = client.post("/auth/google", json={"credential": token()}, headers={"origin": ORIGIN})
    assert signed.status_code == 200 and signed.json() == {"identity": "dave@example.com"}
    cookie = signed.headers["set-cookie"]
    assert cookie.startswith("__session=")
    for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=43200"):
        assert attribute.lower() in cookie.lower(), attribute
    assert client.get("/v1/me").json()["identity"] == "dave@example.com"
    client.get("/v1/goals")
    assert seen[-1].headers["x-mh-actor"] == "dave@example.com"


def test_a_token_google_did_not_issue_for_this_console_signs_nobody_in(tmp_path):
    client, token, _ = _cloud_edge(tmp_path)
    for bad in (
        token(aud="another-client.apps.googleusercontent.com"),
        token(iss="https://evil.example.com"),
        token(verified=False),
        token(ttl=-60),
        "not-a-jwt",
    ):
        response = client.post("/auth/google", json={"credential": bad}, headers={"origin": ORIGIN})
        assert response.status_code == 401, bad
        assert "set-cookie" not in response.headers
    assert client.get("/v1/me").status_code == 401


def test_a_google_account_off_the_access_list_gets_403_and_no_session(tmp_path):
    client, token, _ = _cloud_edge(tmp_path)
    response = client.post("/auth/google", json={"credential": token("eve@example.com")}, headers={"origin": ORIGIN})
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_a_forged_or_expired_session_is_401(tmp_path):
    from meta_harness.edge.app import new_session

    client, _, seen = _cloud_edge(tmp_path)
    client.cookies.set("__session", new_session("dave@example.com", "some-other-key"))
    assert client.get("/v1/me").status_code == 401
    client.cookies.set("__session", new_session("dave@example.com", SESSION_KEY, now=0))
    assert client.get("/v1/me").status_code == 401
    assert seen == []


def test_a_change_from_another_origin_is_refused_even_when_signed_in(tmp_path):
    client, token, seen = _cloud_edge(tmp_path)
    client.post("/auth/google", json={"credential": token()}, headers={"origin": ORIGIN})
    assert client.post("/v1/goals", json={}, headers={"origin": "https://evil.example.com"}).status_code == 403
    assert (
        client.post(
            "/auth/google", json={"credential": token()}, headers={"origin": "https://evil.example.com"}
        ).status_code
        == 403
    )
    assert seen == []
    assert client.post("/v1/goals", json={}, headers={"origin": ORIGIN}).status_code == 200


def test_signing_out_ends_the_session(tmp_path):
    client, token, _ = _cloud_edge(tmp_path)
    client.post("/auth/google", json={"credential": token()}, headers={"origin": ORIGIN})
    out = client.post("/auth/logout", headers={"origin": ORIGIN})
    assert out.status_code == 200 and "__session=" in out.headers["set-cookie"]
    assert client.get("/v1/me").status_code == 401


def test_nothing_signed_in_is_cacheable(tmp_path):
    """Firebase Hosting caches what a response allows; an upstream's public header must not leak through."""
    client, token, _ = _cloud_edge(tmp_path)
    client.post("/auth/google", json={"credential": token()}, headers={"origin": ORIGIN})
    for response in (client.get("/v1/me"), client.get("/v1/goals"), client.get("/auth/config")):
        assert response.headers["cache-control"] == "no-store"
    assert client.get("/auth/config").json() == {"google_client_id": CLIENT_ID}


def test_google_sign_in_without_a_session_key_refuses_to_start(tmp_path):
    import pytest

    with pytest.raises(RuntimeError, match="MH_SESSION_KEY"):
        create_app(dist=tmp_path, operators=set(), google_client_id=CLIENT_ID, session_key="")


def test_locally_there_is_no_sign_in(tmp_path):
    client, _ = _edge(tmp_path)
    assert client.get("/auth/config").json() == {"google_client_id": None}
    assert client.post("/auth/google", json={"credential": "x"}).status_code == 404


# --- The live event stream, straight to Cloud Run with a ticket ---------------------------------------


def _signed_in(tmp_path):
    client, token, seen = _cloud_edge(tmp_path)
    client.post("/auth/google", json={"credential": token()}, headers={"origin": ORIGIN})
    return client, seen


def test_a_signed_in_console_gets_the_stream_address_and_a_ticket(tmp_path):
    client, _ = _signed_in(tmp_path)
    issued = client.post("/auth/stream-ticket", headers={"origin": ORIGIN})
    assert issued.status_code == 200 and issued.headers["cache-control"] == "no-store"
    assert issued.json()["base"] == "https://edge-123.us-central1.run.app"
    assert issued.json()["ticket"]


def test_without_a_session_there_is_no_ticket(tmp_path):
    client, _, _ = _cloud_edge(tmp_path)
    assert client.post("/auth/stream-ticket", headers={"origin": ORIGIN}).status_code == 401


def test_a_ticket_opens_the_stream_and_nothing_else(tmp_path):
    client, seen = _signed_in(tmp_path)
    ticket = client.post("/auth/stream-ticket", headers={"origin": ORIGIN}).json()["ticket"]
    client.cookies.clear()
    # On the Cloud Run address there is no session cookie: the ticket is the credential.
    stream = client.get(f"/v1/events/tail?task_id=t-1&from_position=5&ticket={ticket}", headers={"origin": ORIGIN})
    assert stream.status_code == 200
    assert seen[-1].headers["x-mh-actor"] == "dave@example.com"
    assert "ticket" not in str(seen[-1].url), "the event service must never see the ticket"
    assert "from_position=5" in str(seen[-1].url)
    assert stream.headers["access-control-allow-origin"] == ORIGIN
    forwarded = len(seen)
    assert client.get(f"/v1/goals?ticket={ticket}").status_code == 401
    assert client.get(f"/v1/me?ticket={ticket}").status_code == 401
    assert len(seen) == forwarded


def test_a_ticket_is_not_a_session_and_a_session_is_not_a_ticket(tmp_path):
    from meta_harness.edge.app import new_session, new_ticket

    client, _, seen = _cloud_edge(tmp_path)
    client.cookies.set("__session", new_ticket("dave@example.com", SESSION_KEY))
    assert client.get("/v1/me").status_code == 401
    client.cookies.clear()
    session = new_session("dave@example.com", SESSION_KEY)
    assert client.get(f"/v1/events/tail?ticket={session}").status_code == 401
    assert seen == []


def test_an_expired_or_forged_ticket_does_not_open_the_stream(tmp_path):
    from meta_harness.edge.app import new_ticket

    client, _, seen = _cloud_edge(tmp_path)
    for ticket in (new_ticket("dave@example.com", SESSION_KEY, now=0), new_ticket("dave@example.com", "other-key")):
        assert client.get(f"/v1/events/tail?ticket={ticket}").status_code == 401
    assert seen == []


def test_another_origin_gets_no_cors_grant_to_read_the_stream(tmp_path):
    client, _ = _signed_in(tmp_path)
    ticket = client.post("/auth/stream-ticket", headers={"origin": ORIGIN}).json()["ticket"]
    stream = client.get(f"/v1/events/tail?ticket={ticket}", headers={"origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in stream.headers
    other = client.get("/v1/goals", headers={"origin": ORIGIN})
    assert "access-control-allow-origin" not in other.headers


# --- Through a deploy's restarts ----------------------------------------------------------------------


def _restarting_edge(tmp_path, refusals: int, wait: float):
    """A local edge whose upstream refuses connections `refusals` times, as a restarting service does."""
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) <= refusals:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"ok": True})

    (tmp_path / "index.html").write_text("<div id=root></div>")
    app = create_app(
        dist=tmp_path,
        operators={"dave@example.com"},
        identity="dave@example.com",
        service_key="svc-key",
        transport=httpx.MockTransport(upstream),
        restart_wait_seconds=wait,
    )
    return TestClient(app), seen


def test_a_request_to_a_restarting_service_waits_for_it_and_arrives_once(tmp_path):
    client, seen = _restarting_edge(tmp_path, refusals=3, wait=5)

    response = client.post("/v1/goals", json={"statement": "x"})

    assert response.status_code == 200 and response.json() == {"ok": True}
    assert len(seen) == 4, "three refused connections, then the one that arrived"


def test_a_service_that_stays_down_is_a_503_saying_the_meta_harness_is_updating(tmp_path):
    client, _ = _restarting_edge(tmp_path, refusals=1000, wait=0.5)

    response = client.get("/v1/goals")

    assert response.status_code == 503
    assert "updating" in response.json()["detail"] and response.headers["retry-after"] == "5"


def test_an_admin_reads_logs_from_cloud_logging_with_the_edges_own_token(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("MH_PROJECT", "meta-harness-prod")
    asked: list[httpx.Request] = []

    def google(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        if request.url.host == "metadata.google.internal":
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(
            200,
            json={
                "entries": [
                    {
                        "timestamp": "2026-11-09T10:00:00Z",
                        "severity": "ERROR",
                        "jsonPayload": {"_SYSTEMD_UNIT": "mh-worker.service", "MESSAGE": "run failed"},
                    }
                ]
            },
        )

    (tmp_path / "index.html").write_text("<div id=root></div>")
    app = create_app(
        dist=tmp_path,
        operators={"dave@example.com"},
        identity="dave@example.com",
        service_key="svc-key",
        logs_transport=httpx.MockTransport(google),
    )
    client = TestClient(app)

    shown = client.get("/v1/admin/logs", params={"unit": "mh-worker", "severity": "WARNING", "q": 'say "hi"'}).json()

    assert shown["entries"][0] | {"entry": None} == {
        "time": "2026-11-09T10:00:00Z",
        "service": "mh-worker",
        "severity": "ERROR",
        "message": "run failed",
        "entry": None,
    }
    sent = json.loads(asked[1].content)
    assert asked[1].headers["authorization"] == "Bearer tok"
    assert 'jsonPayload._SYSTEMD_UNIT="mh-worker.service"' in sent["filter"]
    assert "severity>=WARNING" in sent["filter"] and 'SEARCH("say  hi ")' in sent["filter"]
    assert sent["orderBy"] == "timestamp desc" and sent["pageSize"] == 500
    assert client.get("/v1/admin/logs", params={"unit": "mh-nothing"}).status_code == 502


def test_logs_without_google_credentials_say_why(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_PROJECT", "p")
    (tmp_path / "index.html").write_text("")

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    app = create_app(
        dist=tmp_path, operators={"d@x"}, identity="d@x", service_key="k", logs_transport=httpx.MockTransport(offline)
    )
    refused = TestClient(app).get("/v1/admin/logs")
    assert refused.status_code == 502 and "no Google credentials" in refused.json()["detail"]
