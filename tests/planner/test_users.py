"""The console's users: an admin invites, sets roles and deprovisions, and there is always an admin."""

import uuid

import pytest
from fastapi.testclient import TestClient

from meta_harness import db
from meta_harness.planner.service import create_app

from .test_planner import OPERATOR, Events, FakeTasks, FakeTracker, _no_title, migrated, registry  # noqa: F401

pytestmark = pytest.mark.db


@pytest.fixture
def world():
    # Each test starts with no users: the table is the deployment's one access list.
    with db.connect(autocommit=True) as conn:
        conn.execute("DELETE FROM planner.console_user")
    events = Events()
    app = create_app(
        events, FakeTracker(), FakeTasks(), operator_keys={"op-key": "dave@example.com"}, service_keys={}, sweep_seconds=0,
        titler=_no_title,
    )  # fmt: skip
    with TestClient(app) as client:
        yield client, events


def _users(client) -> dict[str, dict]:
    return {u["email"]: u for u in client.get("/v1/admin/users", headers=OPERATOR).json()["users"]}


def test_the_edges_first_access_list_is_taken_once_while_there_are_no_users(world):
    client, _ = world
    first = [{"email": "Dave@Example.com", "role": "admin"}, {"email": "pat@example.com", "role": "user"}]
    seeded = client.post("/v1/admin/users/seed", json={"users": first}, headers=OPERATOR).json()
    assert seeded["added"] == 2
    assert {u["email"]: (u["role"], u["status"]) for u in seeded["users"]} == {
        "dave@example.com": ("admin", "active"),
        "pat@example.com": ("user", "active"),
    }
    again = client.post(
        "/v1/admin/users/seed", json={"users": [{"email": "eve@example.com", "role": "admin"}]}, headers=OPERATOR
    )
    assert again.json()["added"] == 0 and "eve@example.com" not in _users(client)


def test_an_invited_user_is_active_from_their_first_sign_in(world):
    client, events = world
    name = f"new-{uuid.uuid4().hex[:6]}@example.com"
    invited = client.post("/v1/admin/users", json={"email": name, "role": "user"}, headers=OPERATOR)
    assert invited.status_code == 201 and invited.json()["status"] == "invited"
    assert invited.json()["invited_by"] == "dave@example.com" and invited.json()["last_sign_in_at"] is None
    assert client.post("/v1/admin/users", json={"email": name}, headers=OPERATOR).status_code == 409
    assert client.post("/v1/admin/users", json={"email": "not an email"}, headers=OPERATOR).status_code == 422

    client.post(f"/v1/admin/users/{name}/signed-in", headers=OPERATOR)

    user = _users(client)[name]
    assert user["status"] == "active" and user["last_sign_in_at"]
    assert ("user_invited", {"email": name, "role": "user"}) in events.events


def test_roles_change_and_a_deprovisioned_user_can_be_invited_again(world):
    client, events = world
    client.post(
        "/v1/admin/users/seed", json={"users": [{"email": "dave@example.com", "role": "admin"}]}, headers=OPERATOR
    )
    client.post("/v1/admin/users", json={"email": "pat@example.com"}, headers=OPERATOR)

    made = client.patch("/v1/admin/users/pat@example.com", json={"role": "admin"}, headers=OPERATOR)
    assert made.status_code == 200 and made.json()["role"] == "admin"
    gone = client.delete("/v1/admin/users/pat@example.com", headers=OPERATOR)
    assert gone.status_code == 200 and gone.json()["status"] == "deprovisioned"
    client.post("/v1/admin/users/pat@example.com/signed-in", headers=OPERATOR)
    assert _users(client)["pat@example.com"]["status"] == "deprovisioned", "a sign-in report does not bring them back"
    assert client.patch("/v1/admin/users/pat@example.com", json={"role": "user"}, headers=OPERATOR).status_code == 404

    back = client.post("/v1/admin/users", json={"email": "pat@example.com", "role": "user"}, headers=OPERATOR)
    assert back.status_code == 201 and (back.json()["status"], back.json()["role"]) == ("invited", "user")
    kinds = [t for t, _ in events.events]
    assert kinds == ["user_invited", "user_role_changed", "user_deprovisioned", "user_invited"]


def test_the_last_admin_can_be_neither_deprovisioned_nor_made_a_regular_user(world):
    client, _ = world
    client.post(
        "/v1/admin/users/seed", json={"users": [{"email": "dave@example.com", "role": "admin"}]}, headers=OPERATOR
    )
    client.post("/v1/admin/users", json={"email": "pat@example.com", "role": "user"}, headers=OPERATOR)

    for refused in (
        client.delete("/v1/admin/users/dave@example.com", headers=OPERATOR),
        client.patch("/v1/admin/users/dave@example.com", json={"role": "user"}, headers=OPERATOR),
    ):
        assert refused.status_code == 409 and "is the only admin" in refused.json()["detail"]

    client.patch("/v1/admin/users/pat@example.com", json={"role": "admin"}, headers=OPERATOR)
    assert client.patch("/v1/admin/users/dave@example.com", json={"role": "user"}, headers=OPERATOR).status_code == 200
