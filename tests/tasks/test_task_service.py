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


def test_a_task_that_fails_or_ends_unresolved_is_logged_as_an_error_or_a_warning(api, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="meta_harness.tasks")
    failed = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    api.post(f"/v1/tasks/{failed['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
    api.post(
        f"/v1/tasks/{failed['id']}/status",
        json={"status": "failed", "reason": "deploy failed on the merge commit"},
        headers=_as(SERVICE_KEY),
    )
    unresolved = _ended(api, "review_unresolved")

    logged = [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "meta_harness.tasks"]
    assert logged == [
        ("ERROR", f"task {failed['id']} is failed (was running): deploy failed on the merge commit"),
        ("WARNING", f"task {unresolved['id']} is review_unresolved (was running): None"),
    ], "running is not logged; failed is an error; review_unresolved a warning"


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


def test_each_round_says_what_came_before_the_next_round_not_what_came_later(api, events):
    """Two changes rounds each with its own fix run, a clean round reviewed again when the head changed, and a
    clean round that merged: only the last says merged, and each fix link is its own round's."""
    task = _ended(api, "closed")
    tid = task["id"]

    def round_of(n: int, verdict: str) -> list:
        posted = {"round": n, "verdict": verdict, "reviewer": "harness:pi-review/x:y", "head_sha": f"h{n}"}
        return [
            ("review_submitted", {"verdict": verdict, "summary": "", "findings": []}, f"r-review-{n}"),
            ("pull_request_reviewed", posted, None),
        ]

    log = [
        *round_of(1, "changes"),
        ("attempt_finished", {"tier": "fix", "attempt": 3}, "r-fix-1"),
        *round_of(2, "changes"),
        ("attempt_finished", {"tier": "fix", "attempt": 5}, "r-fix-2"),
        *round_of(3, "clean"),
        *round_of(4, "clean"),
        ("pull_request_merged", {"pr_url": "https://github.com/o/r/pull/12"}, None),
    ]
    for kind, payload, run in log:
        events.emit(kind, payload, task_id=tid, run_id=run)
    for position, event in enumerate(events.events, start=1):
        event[2]["position"] = position

    shown = api.get(f"/v1/tasks/{tid}/reviews", headers=_as(CALLER_KEY)).json()["rounds"]

    assert [r["next"] for r in shown] == [
        {"kind": "fix", "run_id": "r-fix-1", "attempt": 3},
        {"kind": "fix", "run_id": "r-fix-2", "attempt": 5},
        {"kind": "reviewed_again"},
        {"kind": "merged"},
    ]


def test_tasks_are_found_by_text_in_their_title_id_or_ref(api):
    word = f"walrus{uuid.uuid4().hex[:6]}"
    task = api.post("/v1/tasks", json=_task(title=f"Fix the {word} crash"), headers=_as(CALLER_KEY)).json()
    found = api.get("/v1/tasks", params={"q": word.upper()}, headers=_as(CALLER_KEY)).json()["tasks"]
    assert [t["id"] for t in found] == [task["id"]]


def test_a_running_task_takes_an_operator_message_and_the_scheduler_reads_it_once(api, events):
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    assert api.post(f"/v1/tasks/{task['id']}/messages", json={"text": "Hi"}, headers=_as(CALLER_KEY)).status_code == 409
    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))

    sent = api.post(
        f"/v1/tasks/{task['id']}/messages",
        json={"text": "Use the helper.", "when": "after_stop"},
        headers=_as(CALLER_KEY),
    )
    stop = api.post(f"/v1/tasks/{task['id']}/messages", json={"when": "stop"}, headers=_as(CALLER_KEY))

    assert sent.status_code == 202 and stop.status_code == 202
    assert [p["when"] for t, p, f in events.events if t == "operator_message" and f.get("task_id") == task["id"]] == [
        "after_stop",
        "stop",
    ]
    assert api.get("/v1/messages/pending", headers=_as(CALLER_KEY)).status_code == 403
    pending = [
        m
        for m in api.get("/v1/messages/pending", headers=_as(SERVICE_KEY)).json()["messages"]
        if m["task_id"] == task["id"]
    ]
    assert [(m["body"], m["deliver"]) for m in pending] == [("Use the helper.", "after_stop"), ("", "stop")]
    for m in pending:
        api.post(f"/v1/messages/{m['id']}/delivered", headers=_as(SERVICE_KEY))
    left = [
        m
        for m in api.get("/v1/messages/pending", headers=_as(SERVICE_KEY)).json()["messages"]
        if m["task_id"] == task["id"]
    ]
    assert left == []
    assert api.post(f"/v1/tasks/{task['id']}/messages", json={"text": " "}, headers=_as(CALLER_KEY)).status_code == 422


def test_a_task_that_has_not_started_takes_several_attempts_and_a_pick_is_its_own_event(api, events):
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    assert task["attempts"] == 1
    assert api.post(f"/v1/tasks/{task['id']}/attempts", json={"count": 5}, headers=_as(CALLER_KEY)).status_code == 422
    assert (
        api.post(f"/v1/tasks/{task['id']}/attempts", json={"count": 3}, headers=_as(CALLER_KEY)).json()["attempts"] == 3
    )
    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
    assert api.post(f"/v1/tasks/{task['id']}/attempts", json={"count": 2}, headers=_as(CALLER_KEY)).status_code == 409

    picked = api.post(f"/v1/tasks/{task['id']}/messages", json={"text": "2", "when": "choose"}, headers=_as(CALLER_KEY))

    assert picked.status_code == 202
    assert [p for t, p, f in events.events if t == "attempt_chosen" and f.get("task_id") == task["id"]][0][
        "attempt"
    ] == 2
    assert (
        api.post(
            f"/v1/tasks/{task['id']}/messages", json={"text": "two", "when": "choose"}, headers=_as(CALLER_KEY)
        ).status_code
        == 422
    )
