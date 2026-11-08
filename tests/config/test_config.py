"""Configuration versions: seeded from the files, checked like the services check them, rolled back, read by the worker."""

import copy
import dataclasses
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meta_harness import db, migrations
from meta_harness.config import api as config_api
from meta_harness.config import kinds, store
from meta_harness.run_service import routing
from meta_harness.run_service.paths import REPO_ROOT

pytestmark = pytest.mark.db

TIERS = json.loads((REPO_ROOT / "config" / "models.json").read_text())["router"]
PROFILES = json.loads((REPO_ROOT / "config" / "harness_profiles.json").read_text())
REPOS = json.loads((REPO_ROOT / "config" / "repos.example.json").read_text())


@pytest.fixture(scope="module", autouse=True)
def migrated():
    try:
        with db.connect() as conn:
            migrations.apply(conn)
    except Exception as err:  # noqa: BLE001
        pytest.skip(f"no Postgres at {db.database_url()}: {err}")


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Each test starts with no versions, and seeds from the shipped files."""
    with db.connect(autocommit=True) as conn:
        conn.execute("TRUNCATE config.active, config.config_version")
    monkeypatch.setitem(
        kinds.KINDS, "repos", dataclasses.replace(kinds.KINDS["repos"], seed=lambda: copy.deepcopy(REPOS))
    )
    store._cache.clear()
    routing._versioned_router.cache_clear()
    yield


class Events:
    def __init__(self):
        self.events = []

    def emit(self, event_type, payload=None, **fields):
        self.events.append((event_type, payload or {}, fields))
        return "id"


@pytest.fixture
def client():
    events = Events()
    app = FastAPI()
    app.state.pool = db.pool()
    app.include_router(config_api.router(lambda: app.state.pool, lambda: "op@example.com", events))
    with TestClient(app) as test_client:
        test_client.events = events
        yield test_client
    app.state.pool.close()


def test_every_kind_is_seeded_from_its_file_on_first_read(client):
    listed = client.get("/v1/config").json()["kinds"]

    assert [k["kind"] for k in listed] == ["tiers", "profiles", "repos"]
    assert all(k["version"] == 1 and k["edited_by"] == "seed" for k in listed)
    assert client.get("/v1/config/tiers").json()["document"] == TIERS
    assert client.get("/v1/config/profiles").json()["document"] == PROFILES


def test_a_saved_edit_becomes_the_next_active_version_with_an_audit_event(client):
    edited = copy.deepcopy(PROFILES)
    edited["profiles"]["pi-lean"]["max_turns"] = 120

    saved = client.put("/v1/config/profiles", json={"document": edited, "note": "more turns"})

    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2
    assert client.get("/v1/config/profiles").json()["document"]["profiles"]["pi-lean"]["max_turns"] == 120
    assert [v["version"] for v in client.get("/v1/config/profiles/versions").json()["versions"]] == [2, 1]
    event_type, payload, fields = client.events.events[-1]
    assert (event_type, payload["kind"], payload["version"], fields["actor"]) == (
        "config_changed",
        "profiles",
        2,
        "op@example.com",
    )


@pytest.mark.parametrize(
    ("kind", "change", "reason"),
    [
        ("tiers", lambda d: d.update(default_tier=None), "default_tier is required"),
        ("tiers", lambda d: d["tiers"][0]["models"].append("claude:claude-sonnet-5"), "is not harness:<profile>"),
        ("tiers", lambda d: d["tiers"][0]["models"].append("harness:pi-ghost/claude:x"), "pi-ghost"),
        ("tiers", lambda d: d["tiers"][0].update(thinking="extreme"), "thinking"),
        ("profiles", lambda d: d["profiles"].pop("pi-lean"), "still names harness profile pi-lean"),
        ("profiles", lambda d: d["profiles"]["pi-lean"].update(tools=["read", "teleport"]), "unknown tools teleport"),
        ("profiles", lambda d: d["profiles"]["pi-lean"].update(max_minutes=0), "max_minutes"),
        ("repos", lambda d: d["repos"].update({"not-a-repo": {}}), "owner/name"),
    ],
)
def test_a_document_the_service_would_refuse_is_refused_with_its_reasons(client, kind, change, reason):
    document = copy.deepcopy(client.get(f"/v1/config/{kind}").json()["document"])
    change(document)

    refused = client.put(f"/v1/config/{kind}", json={"document": document})

    assert refused.status_code == 422
    assert any(reason in r for r in refused.json()["detail"]["reasons"]), refused.json()
    assert client.get(f"/v1/config/{kind}").json()["version"] == 1


def test_rollback_makes_an_earlier_document_active_as_a_new_version(client):
    edited = copy.deepcopy(PROFILES)
    edited["profiles"]["pi-careful"]["max_minutes"] = 90
    client.put("/v1/config/profiles", json={"document": edited})

    rolled = client.post("/v1/config/profiles/rollback", json={"version": 1})

    assert rolled.status_code == 200, rolled.text
    assert (rolled.json()["version"], rolled.json()["rolled_back_from"], rolled.json()["origin"]) == (3, 1, "rollback")
    assert client.get("/v1/config/profiles").json()["document"] == PROFILES
    assert client.post("/v1/config/profiles/rollback", json={"version": 99}).status_code == 404


def test_an_unknown_kind_is_404(client):
    assert client.get("/v1/config/secrets").status_code == 404


def test_the_worker_routes_with_the_active_versions_when_the_source_is_the_database(client, monkeypatch):
    monkeypatch.setenv(store.SOURCE_ENV, "db")
    profiles = copy.deepcopy(PROFILES)
    profiles["profiles"]["pi-swift"] = copy.deepcopy(profiles["profiles"]["pi-lean"])
    assert client.put("/v1/config/profiles", json={"document": profiles}).status_code == 200
    tiers = copy.deepcopy(TIERS)
    tiers["tiers"][0]["models"] = ["harness:pi-swift/claude:claude-sonnet-5"]
    assert client.put("/v1/config/tiers", json={"document": tiers}).status_code == 200

    lines = routing.validate()

    assert lines[0] == "tier standard: harness:pi-swift/claude:claude-sonnet-5, thinking medium"
    assert routing.candidate("harness", "pi-swift/claude:claude-sonnet-5").profile["max_turns"] == 80


def test_the_worker_reads_the_files_by_default(monkeypatch):
    monkeypatch.delenv(store.SOURCE_ENV, raising=False)

    assert routing.profiles() == PROFILES["profiles"]
