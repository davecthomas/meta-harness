import uuid
from decimal import Decimal

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


def _held(api) -> dict:
    task = api.post("/v1/tasks", json=_task(max_usd="10.00"), headers=_as(CALLER_KEY)).json()
    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
    held = api.post(
        f"/v1/tasks/{task['id']}/status",
        json={"status": "running", "reason": "fix run 4 needs about $1.00", "waiting_on": "budget"},
        headers=_as(SERVICE_KEY),
    )
    assert held.status_code == 200
    return held.json()


def test_a_task_held_for_budget_stays_running_is_listed_as_waiting_and_records_the_hold(api, events):
    task = _held(api)

    assert (task["status"], task["waiting_on"]) == ("running", "budget")
    waiting = api.get("/v1/tasks", params={"waiting": "true"}, headers=_as(CALLER_KEY)).json()["tasks"]
    assert task["id"] in [t["id"] for t in waiting]
    assert ("budget_hold", {"reason": "fix run 4 needs about $1.00"}) in [(e[0], e[1]) for e in events.events]
    # Setting it again records nothing more, and leaving running ends the wait.
    api.post(
        f"/v1/tasks/{task['id']}/status",
        json={"status": "running", "reason": "still", "waiting_on": "budget"},
        headers=_as(SERVICE_KEY),
    )
    assert [e[0] for e in events.events].count("budget_hold") == 1
    ended = api.post(f"/v1/tasks/{task['id']}/status", json={"status": "failed"}, headers=_as(SERVICE_KEY)).json()
    assert ended["waiting_on"] is None


def test_raising_a_held_tasks_limit_saves_it_and_queues_the_answer_for_its_workflow(api, events):
    task = _held(api)

    low = api.post(f"/v1/tasks/{task['id']}/budget", json={"action": "raise", "max_usd": "8"}, headers=_as(CALLER_KEY))
    assert low.status_code == 409 and "above the task's $10.00" in low.json()["detail"]
    raised = api.post(
        f"/v1/tasks/{task['id']}/budget", json={"action": "raise", "max_usd": "15"}, headers=_as(CALLER_KEY)
    )

    assert raised.status_code == 202 and Decimal(raised.json()["max_usd"]) == Decimal("15")
    pending = api.get("/v1/messages/pending", headers=_as(SERVICE_KEY)).json()["messages"]
    assert [(m["deliver"], m["body"]) for m in pending if m["task_id"] == task["id"]] == [("budget", "raise:15")]
    assert ("budget_decision", {"action": "raise", "max_usd": "15"}) in [(e[0], e[1]) for e in events.events]


def test_a_budget_answer_needs_a_held_task_and_goes_through_its_own_endpoint(api):
    held = _held(api)
    ended = api.post(f"/v1/tasks/{held['id']}/budget", json={"action": "end"}, headers=_as(CALLER_KEY))
    assert ended.status_code == 202
    through_messages = api.post(
        f"/v1/tasks/{held['id']}/messages", json={"text": "raise:99", "when": "budget"}, headers=_as(CALLER_KEY)
    )
    assert through_messages.status_code == 422

    running = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    api.post(f"/v1/tasks/{running['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
    refused = api.post(f"/v1/tasks/{running['id']}/budget", json={"action": "end"}, headers=_as(CALLER_KEY))
    assert refused.status_code == 409 and "not held for budget" in refused.json()["detail"]


def test_a_retry_sends_an_ended_task_back_to_the_queue_to_resume_at_its_pull_request(api, events):
    task = _ended(api, "review_unresolved")

    retried = api.post(f"/v1/tasks/{task['id']}/retry", json={"max_usd": "20"}, headers=_as(CALLER_KEY))

    assert retried.status_code == 202
    body = retried.json()
    assert (body["status"], body["resume"], body["retries"]) == ("queued", "review", 1)
    assert Decimal(body["max_usd"]) == Decimal("20") and body["status_reason"] == "ops retried it"
    kinds = [(e[0], e[1]) for e in events.events]
    retried_event = next(payload for kind, payload in kinds if kind == "task_retried")
    assert {**retried_event, "max_usd": Decimal(retried_event["max_usd"])} == {
        "action": "retry",
        "resume": "review",
        "max_usd": Decimal("20"),
        "retries": 1,
    }
    assert ("task_status_changed", {"from": "review_unresolved", "to": "queued", "reason": "ops retried it"}) in kinds


def test_a_failed_task_with_no_pull_request_retries_from_a_new_first_run_and_a_canceled_one_is_uncanceled(api):
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "failed"}, headers=_as(SERVICE_KEY))

    retried = api.post(f"/v1/tasks/{task['id']}/retry", headers=_as(CALLER_KEY)).json()
    assert (retried["status"], retried["resume"], retried["retries"]) == ("queued", None, 1)

    api.post(f"/v1/tasks/{task['id']}/cancel", headers=_as(CALLER_KEY))
    again = api.post(f"/v1/tasks/{task['id']}/retry", headers=_as(CALLER_KEY)).json()
    assert (again["status"], again["cancel_requested"], again["retries"]) == ("queued", False, 2)


def test_only_an_ended_task_is_retried_and_only_one_with_a_pull_request_is_merged(api):
    running = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    api.post(f"/v1/tasks/{running['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
    refused = api.post(f"/v1/tasks/{running['id']}/retry", headers=_as(CALLER_KEY))
    assert refused.status_code == 409 and "a running task cannot be retried" in refused.json()["detail"]

    ready = _ended(api, "ready")
    assert api.post(f"/v1/tasks/{ready['id']}/retry", headers=_as(CALLER_KEY)).status_code == 409
    merge = api.post(f"/v1/tasks/{ready['id']}/merge", headers=_as(CALLER_KEY))
    assert merge.status_code == 202
    assert (merge.json()["status"], merge.json()["resume"]) == ("queued", "merge")
    assert merge.json()["status_reason"] == "ops asked for its pull request's merge"


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
    assert shown[0]["findings"] == [{"n": 1, "path": "a.py", "line": 3, "body": "Test it.", "dismissed": None}]
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


def test_a_tasks_spend_lists_its_runs_and_totals_against_its_limit(api, events):
    task = api.post("/v1/tasks", json=_task(max_usd="10.00"), headers=_as(CALLER_KEY)).json()
    tid = task["id"]
    usage = {"calls": 9, "input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 50}
    events.emit("run_finished", {"turns": 8, "usage": usage}, task_id=tid, run_id=f"{tid}:run-1")
    first = {"attempt": 1, "tier": "standard", "candidate": "harness:a/b:c", "spent_usd": "1.25", "status": "FINISHED"}
    events.emit("attempt_finished", first, task_id=tid, run_id=f"{tid}:run-1")
    review = {"attempt": 2, "tier": "review", "candidate": "harness:r/x:y", "spent_usd": "0.25", "status": "FINISHED"}
    events.emit("attempt_finished", review, task_id=tid, run_id=f"{tid}:run-2")
    for position, event in enumerate(events.events, start=1):
        event[2]["position"] = position

    shown = api.get(f"/v1/tasks/{tid}/spend", headers=_as(CALLER_KEY)).json()

    assert Decimal(shown["max_usd"]) == Decimal("10") and Decimal(shown["totals"]["usd"]) == Decimal("1.50")
    assert [(r["kind"], r["turns"], r["usd"]) for r in shown["runs"]] == [("task", 8, "1.25"), ("review", None, "0.25")]
    assert shown["totals"]["by_kind"] == {"task": "1.25", "review": "0.25", "fix": "0"}
    assert api.get("/v1/tasks/t-none/spend", headers=_as(CALLER_KEY)).status_code == 404


def test_a_smoke_task_is_a_services_to_create_and_is_left_out_of_lists_unless_asked(api):
    refused = api.post("/v1/tasks", json=_task(task_type="smoke"), headers=_as(CALLER_KEY))
    assert refused.status_code == 422 and "only the meta-harness creates smoke tasks" in refused.json()["detail"]
    created = api.post("/v1/tasks", json=_task(task_type="smoke"), headers=_as(SERVICE_KEY))
    assert created.status_code == 201
    smoke_id = created.json()["id"]

    listed = api.get("/v1/tasks", params={"q": smoke_id}, headers=_as(SERVICE_KEY)).json()["tasks"]
    assert listed == []
    asked = api.get("/v1/tasks", params={"q": smoke_id, "smoke": "true"}, headers=_as(SERVICE_KEY)).json()["tasks"]
    assert [t["id"] for t in asked] == [smoke_id]
    assert api.get(f"/v1/tasks/{smoke_id}", headers=_as(SERVICE_KEY)).json()["task_type"] == "smoke"


def _holding_review(api) -> dict:
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
    held = api.post(
        f"/v1/tasks/{task['id']}/status",
        json={"status": "running", "reason": "review round 1 asked for changes (2 findings)", "waiting_on": "review"},
        headers=_as(SERVICE_KEY),
    )
    assert held.status_code == 200 and held.json()["waiting_on"] == "review"
    return held.json()


def test_dismissing_a_finding_of_a_held_review_records_it_and_tells_the_workflow(api, events):
    task = _holding_review(api)
    tid = task["id"]
    assert "review_hold" in events.types(tid)

    dismissed = api.post(
        f"/v1/tasks/{tid}/reviews/1/findings/2/dismiss",
        json={"reason": "AGENTS.md requires\nthe bump."},
        headers=_as(CALLER_KEY),
    )
    proceeded = api.post(f"/v1/tasks/{tid}/reviews/1/proceed", headers=_as(CALLER_KEY))

    assert dismissed.status_code == 202 and proceeded.status_code == 202
    pending = api.get("/v1/messages/pending", headers=_as(SERVICE_KEY)).json()["messages"]
    mine = [(m["deliver"], m["body"]) for m in pending if m["task_id"] == tid]
    assert mine == [("review", "dismiss:1:2:AGENTS.md requires the bump."), ("review", "proceed:1")]
    recorded = [(kind, payload) for kind, payload, fields in events.events if fields.get("task_id") == tid]
    assert ("finding_dismissed", {"round": 1, "finding": 2, "reason": "AGENTS.md requires the bump."}) in recorded
    assert ("review_proceeded", {"round": 1}) in recorded


def test_a_review_is_answered_only_while_its_task_holds_it_and_through_its_own_endpoints(api):
    running = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    api.post(f"/v1/tasks/{running['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
    refused = api.post(f"/v1/tasks/{running['id']}/reviews/1/proceed", headers=_as(CALLER_KEY))
    assert refused.status_code == 409 and "not holding a review" in refused.json()["detail"]

    held = _holding_review(api)
    no_reason = api.post(
        f"/v1/tasks/{held['id']}/reviews/1/findings/1/dismiss", json={"reason": ""}, headers=_as(CALLER_KEY)
    )
    assert no_reason.status_code == 422
    through_messages = api.post(
        f"/v1/tasks/{held['id']}/messages", json={"text": "proceed:1", "when": "review"}, headers=_as(CALLER_KEY)
    )
    assert through_messages.status_code == 422


def test_a_rounds_findings_are_numbered_and_say_who_dismissed_them(api, events):
    task = _ended(api, "ready")
    tid = task["id"]
    findings = [
        {"path": "a.py", "line": 3, "body": "Test it."},
        {"path": None, "line": None, "body": "Revert the bump."},
    ]
    events.emit(
        "review_submitted", {"verdict": "changes", "summary": "Two.", "findings": findings}, task_id=tid, run_id="r-2"
    )
    events.emit("pull_request_reviewed", {"round": 1, "verdict": "changes", "head_sha": "abc"}, task_id=tid)
    events.emit(
        "finding_dismissed", {"round": 1, "finding": 2, "reason": "The rule requires it."}, task_id=tid, actor="dave"
    )
    for position, event in enumerate(events.events, start=1):
        event[2]["position"] = position

    [shown] = api.get(f"/v1/tasks/{tid}/reviews", headers=_as(CALLER_KEY)).json()["rounds"]

    assert [(f["n"], f["dismissed"]) for f in shown["findings"]] == [
        (1, None),
        (2, {"by": "dave", "reason": "The rule requires it."}),
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
