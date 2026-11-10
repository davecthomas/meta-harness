import uuid

import pytest
from fastapi.testclient import TestClient

from meta_harness import db, migrations
from meta_harness.tasks.service import create_app

pytestmark = pytest.mark.db

CALLER_KEY = "caller-key"
OTHER_KEY = "other-key"
SERVICE_KEY = "service-key"


class RecordedEvents:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, dict]] = []

    def emit(self, event_type, payload=None, **fields):
        self.events.append((event_type, payload or {}, fields))
        return str(uuid.uuid4())

    def types(self, task_id: str) -> list[str]:
        return [t for t, _, f in self.events if f.get("task_id") == task_id]

    def read(self, from_position=0, limit=500, before_position=None, **filters):
        found = [
            {"event_type": t, "payload": p, **f}
            for t, p, f in self.events
            if all(str(f.get(k, t if k == "event_type" else None)) in v.split(",") for k, v in filters.items())
        ]
        return {"events": found[:limit]}


@pytest.fixture(scope="module", autouse=True)
def migrated():
    try:
        with db.connect() as conn:
            migrations.apply(conn)
    except Exception as err:  # noqa: BLE001
        pytest.skip(f"no Postgres at {db.database_url()}: {err}")


@pytest.fixture
def events() -> RecordedEvents:
    return RecordedEvents()


@pytest.fixture
def api(events):
    app = create_app(
        events,
        caller_keys={CALLER_KEY: "ops", OTHER_KEY: "someone"},
        service_keys={SERVICE_KEY: "run-service"},
    )
    with TestClient(app) as client:
        yield client


def _as(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _task(**overrides) -> dict:
    body = {
        "external_ref": f"ref-{uuid.uuid4().hex[:8]}",
        "repo": "davecthomas/meta-harness",
        "task_type": "bug",
        "title": "Fix the thing",
        "description": "It is broken.",
        "acceptance_checks": ["make test"],
    }
    return {**body, **overrides}


def test_a_task_needs_a_known_key(api):
    assert api.post("/v1/tasks", json=_task()).status_code == 401
    assert api.post("/v1/tasks", json=_task(), headers=_as("nope")).status_code == 401


def test_create_is_idempotent_on_the_callers_external_ref(api, events):
    body = _task()
    first = api.post("/v1/tasks", json=body, headers=_as(CALLER_KEY))
    again = api.post("/v1/tasks", json=body, headers=_as(CALLER_KEY))
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["id"] == again.json()["id"]
    assert first.json()["status"] == "queued"
    assert events.types(first.json()["id"]) == ["task_created"]
    found = api.get("/v1/tasks", params={"external_ref": body["external_ref"]}, headers=_as(CALLER_KEY))
    assert found.json()["id"] == first.json()["id"]


def test_a_caller_sees_only_its_own_tasks(api):
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    assert api.get(f"/v1/tasks/{task['id']}", headers=_as(OTHER_KEY)).status_code == 404
    assert api.get(f"/v1/tasks/{task['id']}", headers=_as(SERVICE_KEY)).status_code == 200


def test_only_the_meta_harness_creates_review_and_fix_tasks(api):
    parent = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    review = _task(task_type="review", parent_task_id=parent["id"])
    assert api.post("/v1/tasks", json=review, headers=_as(CALLER_KEY)).status_code == 422
    assert api.post("/v1/tasks", json=review, headers=_as(SERVICE_KEY)).status_code == 201


def test_a_task_with_dependencies_starts_blocked(api):
    task = api.post("/v1/tasks", json=_task(depends_on=["github:o/r#1"]), headers=_as(CALLER_KEY)).json()
    assert task["status"] == "blocked"


def test_status_moves_follow_the_state_machine(api, events):
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    status = f"/v1/tasks/{task['id']}/status"
    assert api.post(status, json={"status": "running"}, headers=_as(CALLER_KEY)).status_code == 403
    assert api.post(status, json={"status": "closed"}, headers=_as(SERVICE_KEY)).status_code == 409
    running = api.post(status, json={"status": "running"}, headers=_as(SERVICE_KEY)).json()
    assert running["status"] == "running"
    closed = api.post(
        status, json={"status": "closed", "pr_url": "https://github.com/o/r/pull/1"}, headers=_as(SERVICE_KEY)
    ).json()
    assert (closed["status"], closed["pr_url"]) == ("closed", "https://github.com/o/r/pull/1")
    assert api.post(status, json={"status": "running"}, headers=_as(SERVICE_KEY)).status_code == 409
    assert events.types(task["id"]) == ["task_created", "task_status_changed", "task_status_changed"]


def test_cancel_ends_a_queued_task_and_flags_a_running_one(api, events):
    queued = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    canceled = api.post(f"/v1/tasks/{queued['id']}/cancel", headers=_as(CALLER_KEY)).json()
    assert canceled["status"] == "canceled"

    running = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    api.post(f"/v1/tasks/{running['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
    flagged = api.post(f"/v1/tasks/{running['id']}/cancel", headers=_as(CALLER_KEY)).json()
    assert (flagged["status"], flagged["cancel_requested"]) == ("running", True)
    assert events.types(running["id"])[-1] == "task_cancel_requested"
    again = api.get(f"/v1/tasks/{running['id']}", headers=_as(CALLER_KEY)).json()
    assert again["cancel_requested"] is True


def test_a_running_task_keeps_its_pull_request_and_the_list_finds_it_by_pr(api, events):
    task = api.post("/v1/tasks", json=_task(), headers=_as(SERVICE_KEY)).json()
    status = f"/v1/tasks/{task['id']}/status"
    api.post(status, json={"status": "running"}, headers=_as(SERVICE_KEY))
    changes = len([e for e in events.events if e[0] == "task_status_changed"])

    saved = api.post(
        status,
        json={"status": "running", "pr_url": "https://github.com/o/r/pull/9", "head_sha": "abc"},
        headers=_as(SERVICE_KEY),
    ).json()

    assert (saved["status"], saved["pr_url"], saved["head_sha"]) == ("running", "https://github.com/o/r/pull/9", "abc")
    assert len([e for e in events.events if e[0] == "task_status_changed"]) == changes, "no status changed"
    with_pr = api.get("/v1/tasks", params={"status": "running", "pr": "true", "limit": 1000}, headers=_as(SERVICE_KEY))
    without = api.get("/v1/tasks", params={"status": "running", "pr": "false", "limit": 1000}, headers=_as(SERVICE_KEY))
    assert task["id"] in {t["id"] for t in with_pr.json()["tasks"]}
    assert task["id"] not in {t["id"] for t in without.json()["tasks"]}


def _ended(api, status: str) -> dict:
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
    api.post(
        f"/v1/tasks/{task['id']}/status",
        json={"status": status, "pr_url": "https://github.com/o/r/pull/12"},
        headers=_as(SERVICE_KEY),
    )
    return task


def test_clearing_a_task_whose_pull_request_merged_closes_it(api, events):
    task = _ended(api, "review_unresolved")
    events.emit("pull_request_merged", {"pr_url": "https://github.com/o/r/pull/12"}, task_id=task["id"])

    cleared = api.post(f"/v1/tasks/{task['id']}/clear", headers=_as(CALLER_KEY)).json()

    assert (cleared["status"], cleared["status_reason"]) == ("closed", "its pull request #12 merged")


def test_clearing_a_task_whose_pull_request_did_not_merge_cancels_it_and_says_who(api):
    task = _ended(api, "ready")

    cleared = api.post(f"/v1/tasks/{task['id']}/clear", headers=_as(CALLER_KEY)).json()

    assert cleared["status"] == "canceled"
    assert cleared["status_reason"] == "cleared by ops with pull request #12 not merged"
    again = api.post(f"/v1/tasks/{task['id']}/clear", headers=_as(CALLER_KEY))
    assert again.status_code == 409


def test_reviews_pair_each_posted_round_with_its_findings_and_say_what_came_next(api, events):
    task = _ended(api, "ready")
    tid = task["id"]
    log = [
        (
            "review_submitted",
            {
                "verdict": "changes",
                "summary": "Two gaps.",
                "findings": [{"path": "a.py", "line": 3, "body": "Test it."}],
            },
            "r-2",
        ),
        (
            "pull_request_reviewed",
            {
                "round": 1,
                "verdict": "changes",
                "reviewer": "harness:pi-review/claude:claude-opus-5-5",
                "head_sha": "abc",
                "review_url": "u1",
            },
            None,
        ),
        ("attempt_finished", {"tier": "fix", "attempt": 3}, "r-3"),
        ("review_submitted", {"verdict": "clean", "summary": "Good now.", "findings": []}, "r-4"),
        (
            "pull_request_reviewed",
            {
                "round": 2,
                "verdict": "clean",
                "reviewer": "harness:pi-review/claude:claude-opus-5-5",
                "head_sha": "def",
                "review_url": "u2",
            },
            None,
        ),
    ]
    for kind, payload, run in log:
        events.emit(kind, payload, task_id=tid, run_id=run)
    for position, event in enumerate(events.events, start=1):
        event[2]["position"] = position

    shown = api.get(f"/v1/tasks/{tid}/reviews", headers=_as(CALLER_KEY)).json()["rounds"]

    assert [(r["round"], r["verdict"], r["run_id"]) for r in shown] == [(1, "changes", "r-2"), (2, "clean", "r-4")]
    assert shown[0]["findings"] == [{"path": "a.py", "line": 3, "body": "Test it."}]
    assert shown[0]["next"] == {"kind": "fix", "run_id": "r-3", "attempt": 3}
    assert shown[1]["next"]["kind"] == "waiting"
