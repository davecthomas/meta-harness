"""The edge decides who may use the console, and who is an admin, by the planner's users."""

import httpx
from fastapi.testclient import TestClient

from meta_harness.edge.app import create_app
from meta_harness.edge.directory import Directory


class Planner:
    """The planner's users routes, as the edge calls them."""

    def __init__(self, users: list[dict] | None = None, down: bool = False):
        self.users = users or []
        self.down = down
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if self.down:
            raise httpx.ConnectError("refused")
        if request.url.path.endswith("/seed"):
            import json

            if not self.users:
                self.users = [{**u, "status": "active"} for u in json.loads(request.content)["users"]]
            return httpx.Response(200, json={"added": len(self.users), "users": self.users})
        if request.url.path.endswith("/signed-in"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"users": self.users})


def _directory(planner: Planner, operators=("dave@example.com",), admins=(), refresh: float = 0.0) -> Directory:
    return Directory(
        "http://planner", "key", set(operators), set(admins), httpx.MockTransport(planner), refresh_seconds=refresh
    )


def test_the_first_access_list_seeds_the_planners_users_and_every_operator_is_an_admin_without_an_admin_list():
    planner = Planner()
    directory = _directory(planner, operators=("dave@example.com", "pat@example.com"))

    assert directory.allowed("dave@example.com") and directory.is_admin("pat@example.com")
    assert ("POST", "/v1/admin/users/seed") in planner.calls
    assert {u["email"]: u["role"] for u in planner.users} == {"dave@example.com": "admin", "pat@example.com": "admin"}


def test_the_planners_users_decide_once_there_are_some():
    planner = Planner(
        [
            {"email": "dave@example.com", "role": "admin", "status": "active"},
            {"email": "pat@example.com", "role": "user", "status": "invited"},
            {"email": "gone@example.com", "role": "admin", "status": "deprovisioned"},
        ]
    )
    # The first access list names someone the users do not: the users decide.
    directory = _directory(planner, operators=("dave@example.com", "old@example.com"))

    assert directory.allowed("pat@example.com") and not directory.is_admin("pat@example.com")
    assert directory.is_admin("dave@example.com")
    assert not directory.allowed("gone@example.com") and not directory.is_admin("gone@example.com")
    assert not directory.allowed("old@example.com")
    assert ("POST", "/v1/admin/users/seed") not in planner.calls


def test_an_edge_that_has_never_read_the_users_lets_nobody_in_and_asks_again_at_once():
    """A fresh edge with the planner down does not know who was deprovisioned or who is an admin. The first
    access list still names a user who has since been removed, so it decides nothing."""
    planner = Planner(down=True)
    directory = _directory(planner, operators=("gone@example.com",), admins=(), refresh=3600.0)
    directory.read_at = 0.0

    assert not directory.ready()
    assert not directory.allowed("gone@example.com") and not directory.is_admin("gone@example.com")

    # The planner is back with its own users: the next request is decided by them, with no wait.
    planner.down = False
    planner.users = [{"email": "dave@example.com", "role": "admin", "status": "active"}]
    directory.read_at -= 2
    assert directory.ready() and directory.is_admin("dave@example.com")
    assert not directory.allowed("gone@example.com")


def test_a_planner_that_cannot_be_reached_leaves_the_last_list_deciding():
    planner = Planner([{"email": "pat@example.com", "role": "user", "status": "active"}])
    directory = _directory(planner, admins=("dave@example.com",))
    assert directory.allowed("pat@example.com")

    planner.down = True
    assert directory.ready() and directory.allowed("pat@example.com"), "the users last read go on deciding"
    assert not directory.allowed("dave@example.com"), "and not the first access list"


def test_a_sign_in_the_planner_could_not_be_told_of_is_told_once_it_can_be_reached():
    """A sign-in during a deploy, with the planner down, would otherwise show as Never for the session's days."""
    planner = Planner([{"email": "dave@example.com", "role": "admin", "status": "active"}])
    directory = _directory(planner)
    assert directory.allowed("dave@example.com")
    planner.down = True
    directory.signed_in("dave@example.com")
    assert directory.allowed("dave@example.com")

    planner.down = False
    planner.calls.clear()
    assert directory.allowed("dave@example.com")
    assert ("POST", "/v1/admin/users/dave@example.com/signed-in") in planner.calls

    planner.calls.clear()
    directory.allowed("dave@example.com")
    assert ("POST", "/v1/admin/users/dave@example.com/signed-in") not in planner.calls, "told only once"


def test_the_edge_answers_503_with_retry_after_until_it_has_read_the_users(tmp_path):
    planner = Planner(down=True)
    forwarded: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        forwarded.append(request)
        return httpx.Response(200, json={})

    app = create_app(
        dist=tmp_path, identity="gone@example.com", operators=set(), service_key="key",
        transport=httpx.MockTransport(upstream), directory=_directory(planner, operators=("gone@example.com",)),
    )  # fmt: skip
    client = TestClient(app)

    refused = client.get("/v1/goals")

    assert refused.status_code == 503 and refused.headers["Retry-After"] == "2"
    assert "could not be read yet" in refused.json()["detail"] and "Try again" in refused.json()["detail"]
    assert client.get("/v1/me").status_code == 503
    assert forwarded == [], "nothing is forwarded, with any role, on a guess"

    planner.down = False
    planner.users = [{"email": "dave@example.com", "role": "admin", "status": "active"}]
    assert client.get("/v1/goals").status_code == 403, "the removed user is refused once the users are read"


def test_a_deprovisioned_users_next_request_is_refused_and_a_regular_user_opens_no_admin_path(tmp_path):
    planner = Planner(
        [
            {"email": "dave@example.com", "role": "admin", "status": "active"},
            {"email": "pat@example.com", "role": "user", "status": "active"},
        ]
    )
    upstream = httpx.MockTransport(lambda request: httpx.Response(200, json={"users": planner.users}))

    def edge(identity: str) -> TestClient:
        app = create_app(
            dist=tmp_path, identity=identity, operators=set(), service_key="key", transport=upstream,
            directory=_directory(planner, operators=()),
        )  # fmt: skip
        return TestClient(app)

    pat, dave = edge("pat@example.com"), edge("dave@example.com")
    assert pat.get("/v1/me").json()["roles"] == ["operator"]
    assert pat.get("/v1/admin/users").status_code == 403
    assert dave.get("/v1/admin/users").status_code == 200
    # The seed and the sign-in report are the edge's own calls.
    assert dave.post("/v1/admin/users/seed", json={"users": []}).status_code == 404

    planner.users[1]["status"] = "deprovisioned"
    refused = pat.get("/v1/me")
    assert refused.status_code == 403 and "not on the console's access list" in refused.json()["detail"]


def test_a_path_with_dot_segments_or_a_service_only_call_is_refused_before_it_is_forwarded(tmp_path) -> None:
    """The role checks read the path as sent and the HTTP client collapses `..` when it forwards, so a regular
    user could reach an admin route as /v1/goals/../admin/users; and the edge forwards with its service key, so
    a browser could append events or set a task's status."""
    planner = Planner(
        [
            {"email": "dave@example.com", "role": "admin", "status": "active"},
            {"email": "pat@example.com", "role": "user", "status": "active"},
        ]
    )
    forwarded: list[tuple[str, str]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        forwarded.append((request.method, request.url.path))
        return httpx.Response(200, json={})

    def edge(identity: str) -> TestClient:
        app = create_app(
            dist=tmp_path, identity=identity, operators=set(), service_key="key",
            transport=httpx.MockTransport(answer), directory=_directory(planner, operators=()),
        )  # fmt: skip
        return TestClient(app)

    pat, dave = edge("pat@example.com"), edge("dave@example.com")
    for path in ("/v1/goals/%2e%2e/admin/users/pat@example.com", "/v1/goals/%2e%2e/config/repos", "/v1/goals//x"):
        refused = pat.patch(path, json={"role": "admin"})
        assert refused.status_code == 400 and "'..'" in refused.json()["detail"], path
    # An admin's browser holds no service key either.
    for client in (pat, dave):
        assert client.post("/v1/events", json={"type": "attempt_finished"}).status_code == 403
        for call in ("status", "recover", "drive", "name"):
            assert client.post(f"/v1/tasks/t-1/{call}", json={}).status_code == 403
        assert client.get("/v1/tasks/unnamed").status_code == 403
    assert forwarded == []

    assert pat.get("/v1/events").status_code == 200
    assert pat.post("/v1/tasks/t-1/retry", json={}).status_code == 200
    assert forwarded == [("GET", "/v1/events"), ("POST", "/v1/tasks/t-1/retry")]

    from meta_harness.edge.app import canonical

    assert canonical("/v1/goals/g-1") and not canonical("/v1/goals/../admin/users") and not canonical("/v1/a/./b")
