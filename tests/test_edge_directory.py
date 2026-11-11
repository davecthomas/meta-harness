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


def test_a_planner_that_cannot_be_reached_leaves_the_last_list_deciding():
    planner = Planner(down=True)
    directory = _directory(planner, admins=("dave@example.com",))
    assert directory.allowed("dave@example.com") and directory.is_admin("dave@example.com")
    assert not directory.allowed("pat@example.com")

    planner.down = False
    planner.users = [{"email": "pat@example.com", "role": "user", "status": "active"}]
    assert directory.allowed("pat@example.com")
    planner.down = True
    assert directory.allowed("pat@example.com"), "the users last read go on deciding"


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
