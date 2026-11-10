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

    assert [k["kind"] for k in listed] == ["tiers", "profiles", "repos", "task_policy"]
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


# --- What the shipped files add -----------------------------------------------------------------------


def _ship_profiles_without(monkeypatch, name: str) -> None:
    """Seed as if the deployment first ran code that did not have profile `name` yet."""
    older = copy.deepcopy(PROFILES)
    del older["profiles"][name]
    monkeypatch.setitem(
        kinds.KINDS, "profiles", dataclasses.replace(kinds.KINDS["profiles"], seed=lambda: copy.deepcopy(older))
    )


def _ship_profiles(monkeypatch, document: dict) -> None:
    monkeypatch.setitem(
        kinds.KINDS, "profiles", dataclasses.replace(kinds.KINDS["profiles"], seed=lambda: copy.deepcopy(document))
    )


def test_a_profile_shipped_after_the_first_seed_is_added_as_a_new_version(monkeypatch):
    _ship_profiles_without(monkeypatch, "pi-review")
    with db.connect(autocommit=True) as conn:
        first = store.active(conn, "profiles")
        assert "pi-review" not in first["document"]["profiles"]
        _ship_profiles(monkeypatch, PROFILES)

        saved = store.add_shipped(conn)

        assert [(s["kind"], s["origin"], s["edited_by"]) for s in saved] == [("profiles", "shipped", "meta-harness")]
        assert saved[0]["note"] == "added from the shipped file: pi-review"
        now = store.active(conn, "profiles")
        assert now["version"] == first["version"] + 1
        assert now["document"]["profiles"]["pi-review"] == PROFILES["profiles"]["pi-review"]
        assert set(now["document"]["profiles"]) == set(PROFILES["profiles"])
        assert store.add_shipped(conn) == [], "a second start adds nothing"


def test_an_operator_change_is_kept_and_a_removed_profile_stays_removed(monkeypatch):
    with db.connect(autocommit=True) as conn:
        seeded = store.active(conn, "profiles")["document"]
        edited = copy.deepcopy(seeded)
        del edited["profiles"]["pi-review"]
        edited["profiles"]["pi-lean"]["max_turns"] = 12
        store.save(conn, "profiles", edited, "op@example.com", "no reviews here")

        assert store.add_shipped(conn) == []

        now = store.active(conn, "profiles")["document"]["profiles"]
        assert "pi-review" not in now and now["pi-lean"]["max_turns"] == 12


def test_a_shipped_profile_the_check_would_refuse_is_not_added(monkeypatch):
    with db.connect(autocommit=True) as conn:
        before = store.active(conn, "profiles")
        broken = copy.deepcopy(PROFILES)
        broken["profiles"]["pi-broken"] = {"tools": ["teleport"], "max_turns": 5, "max_minutes": 5}
        _ship_profiles(monkeypatch, broken)

        assert store.add_shipped(conn) == []
        assert store.active(conn, "profiles")["version"] == before["version"]


def test_the_planner_adds_shipped_profiles_at_startup_and_records_it(monkeypatch):
    from meta_harness.planner.service import add_shipped_configuration

    _ship_profiles_without(monkeypatch, "pi-review")
    with db.connect(autocommit=True) as conn:
        store.active(conn, "profiles")
    _ship_profiles(monkeypatch, PROFILES)
    events = Events()

    add_shipped_configuration(db.pool(), events)

    assert [(t, p["kind"], p["origin"], f["actor"]) for t, p, f in events.events] == [
        ("config_changed", "profiles", "shipped", "meta-harness")
    ]


def test_a_tier_candidate_on_the_openai_engine_is_refused_with_the_engine_to_use(client):
    tiers = copy.deepcopy(TIERS)
    tiers["tiers"][1]["models"][1] = "harness:pi-careful/openai:gpt-6-sol"

    response = client.put("/v1/config/tiers", json={"document": tiers, "note": "chat completions"})

    assert response.status_code == 422
    assert any("name openai-responses" in reason for reason in response.json()["detail"]["reasons"])


def test_the_active_tiers_move_off_the_openai_engine_once_and_keep_everything_else(monkeypatch):
    old = copy.deepcopy(TIERS)
    old["tiers"][1]["models"][1] = "harness:pi-careful/openai:gpt-6-sol"
    monkeypatch.setitem(
        kinds.KINDS, "tiers", dataclasses.replace(kinds.KINDS["tiers"], seed=lambda: copy.deepcopy(old))
    )
    with db.connect(autocommit=True) as conn:
        first = store.active(conn, "tiers")

        saved = store.replace_engines(conn)

        assert [(s["origin"], s["edited_by"]) for s in saved] == [("shipped", "meta-harness")]
        assert (
            "harness:pi-careful/openai:gpt-6-sol to harness:pi-careful/openai-responses:gpt-6-sol" in saved[0]["note"]
        )
        now = store.active(conn, "tiers")
        assert now["version"] == first["version"] + 1
        assert now["document"]["tiers"][1]["models"][1] == "harness:pi-careful/openai-responses:gpt-6-sol"
        assert now["document"]["tiers"][0] == old["tiers"][0]
        assert store.replace_engines(conn) == [], "a second start changes nothing"


def test_the_model_catalog_offers_priced_chat_models_on_the_engines_a_harness_uses(client):
    names = {m["name"] for m in client.get("/v1/config/models").json()["models"]}
    assert "claude:claude-sonnet-5" in names and "openai-responses:gpt-6-sol" in names
    assert not any(n.startswith("openai:") for n in names), "OpenAI is reached through openai-responses"
    assert not any("embedding" in n or "/" in n.split(":", 1)[1] for n in names)


def test_the_task_policy_is_a_kind_whose_parallel_limit_the_vm_caps(client, monkeypatch):
    monkeypatch.setenv("MH_MAX_SANDBOXES", "8")
    policy = client.get("/v1/config/task_policy").json()["document"]
    assert policy["max_parallel"] == 8
    assert client.get("/v1/config").json()["max_sandboxes"] == 8

    too_many = {**policy, "max_parallel": 9}
    refused = client.put("/v1/config/task_policy", json={"document": too_many})
    assert refused.status_code == 422
    assert refused.json()["detail"]["reasons"] == [
        "max_parallel 9 is more than the 8 sandboxes this deployment's VM holds"
    ]

    gone = {**policy, "review": {**policy["review"], "reviewers": ["harness:pi-gone/claude:claude-opus-5-5"]}}
    reasons = client.put("/v1/config/task_policy", json={"document": gone}).json()["detail"]["reasons"]
    assert reasons == [
        "reviewer harness:pi-gone/claude:claude-opus-5-5 names harness profile pi-gone, which the profiles lack"
    ]

    assert client.put("/v1/config/task_policy", json={"document": {**policy, "max_parallel": 3}}).status_code == 200


def test_the_scheduler_reads_max_parallel_from_the_active_version(client, monkeypatch):
    monkeypatch.setenv("MH_CONFIG_SOURCE", "db")
    monkeypatch.delenv("MH_MAX_SANDBOXES", raising=False)
    policy = client.get("/v1/config/task_policy").json()["document"]
    client.put("/v1/config/task_policy", json={"document": {**policy, "max_parallel": 5}})

    assert store.max_parallel() == 5
    monkeypatch.setenv("MH_MAX_SANDBOXES", "4")
    with pytest.raises(kinds.ConfigError, match="task_policy: max_parallel 5 is more than the 4 sandboxes"):
        store.max_parallel()
    monkeypatch.setenv("MH_MAX_SANDBOXES", "lots")
    with pytest.raises(kinds.ConfigError, match="MH_MAX_SANDBOXES must be a positive whole number"):
        store.max_parallel()
