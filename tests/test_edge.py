import httpx
from fastapi.testclient import TestClient

from meta_harness.edge.app import create_app


def _edge(tmp_path, identity="dave@example.com", operators=("dave@example.com",)):
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


def test_me_names_the_operator(tmp_path):
    client, _ = _edge(tmp_path)
    assert client.get("/v1/me").json() == {"identity": "dave@example.com"}


def test_the_app_and_its_routes_load_from_dist(tmp_path):
    client, _ = _edge(tmp_path)
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert "root" in client.get("/goals/g-1").text
    assert client.get("/../pyproject.toml").status_code in (200, 404)
    assert "root" in client.get("/../pyproject.toml").text or client.get("/../pyproject.toml").status_code == 404


def test_behind_iap_the_operator_is_the_verified_email(tmp_path, monkeypatch):
    import time

    import jwt
    from cryptography.hazmat.primitives.asymmetric import ec

    from meta_harness.edge import app as edge

    key = ec.generate_private_key(ec.SECP256R1())

    class Keys:
        def __init__(self, *args, **kwargs):
            pass

        def get_signing_key_from_jwt(self, token):
            return type("SigningKey", (), {"key": key.public_key()})()

    monkeypatch.setattr(edge.jwt, "PyJWKClient", Keys)
    audience = "/projects/123/locations/us-central1/services/edge"
    monkeypatch.setenv("MH_EDGE_IAP_AUDIENCE", audience)
    (tmp_path / "index.html").write_text("<div id=root></div>")
    client = TestClient(
        create_app(
            dist=tmp_path,
            operators={"dave@example.com"},
            identity="",
            service_key="k",
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        )
    )

    def assertion(email, aud=audience):
        now = int(time.time())
        claims = {
            "email": f"accounts.google.com:{email}",
            "aud": aud,
            "iss": "https://cloud.google.com/iap",
            "iat": now,
            "exp": now + 600,
        }
        return jwt.encode(claims, key, algorithm="ES256")

    assert client.get("/v1/me").status_code == 401
    assert client.get("/v1/me", headers={"x-goog-iap-jwt-assertion": assertion("dave@example.com")}).json() == {
        "identity": "dave@example.com"
    }
    assert client.get("/v1/me", headers={"x-goog-iap-jwt-assertion": assertion("eve@example.com")}).status_code == 403
    assert (
        client.get(
            "/v1/me", headers={"x-goog-iap-jwt-assertion": assertion("dave@example.com", aud="/other")}
        ).status_code
        == 401
    )
