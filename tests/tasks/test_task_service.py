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
            # The test database outlives a run, and the pending list holds 100: messages earlier runs left
            # undelivered would crowd this run's out of it.
            conn.execute("UPDATE tasks.task_message SET delivered_at = now() WHERE delivered_at IS NULL")
            # The same for the list of tasks waiting on an operator, which holds 100 by default.
            conn.execute("UPDATE tasks.task SET waiting_on = NULL WHERE waiting_on IS NOT NULL")
            conn.commit()
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


def test_a_callers_key_names_only_a_registered_repo_and_puts_no_task_into_a_goal(api):
    """A caller's key is not the planner's: with one, a task could name any repo the App is installed on, and
    charge another goal's budget."""
    unlisted = api.post("/v1/tasks", json=_task(repo="someone/unregistered"), headers=_as(CALLER_KEY))
    assert unlisted.status_code == 422
    assert "someone/unregistered is not in the repo registry" in unlisted.json()["detail"]
    assert "Repos page" in unlisted.json()["detail"], "the refusal says what to do"

    into_goal = api.post("/v1/tasks", json=_task(goal_id="g-someone-elses"), headers=_as(CALLER_KEY))
    assert into_goal.status_code == 422 and "goal_id is the planner's to set" in into_goal.json()["detail"]

    # The planner's own submissions are as before: its goals, and the repos its registry gave it.
    planners = api.post("/v1/tasks", json=_task(repo="someone/unregistered", goal_id="g-1"), headers=_as(SERVICE_KEY))
    assert planners.status_code == 201, planners.text


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


def test_a_task_held_for_budget_is_read_with_what_it_asks_for_and_any_other_task_with_none(api):
    from meta_harness import budget_request

    task = _held(api)
    # A hold whose reason is not the workflow's sentence has no amounts to offer.
    assert api.get(f"/v1/tasks/{task['id']}", headers=_as(CALLER_KEY)).json()["budget_request"] is None

    reason = budget_request.hold_reason("fix run 4", "2.5", budget_request.task_why("0.75", "10"))
    api.post(
        f"/v1/tasks/{task['id']}/status",
        json={"status": "running", "reason": reason, "waiting_on": "budget"},
        headers=_as(SERVICE_KEY),
    )
    asked = api.get(f"/v1/tasks/{task['id']}", headers=_as(CALLER_KEY)).json()["budget_request"]
    assert (asked["for"], asked["add_usd"], asked["to_usd"]) == ("task", "2", "12.00")
    listed = api.get("/v1/tasks", params={"waiting": "true"}, headers=_as(CALLER_KEY)).json()["tasks"]
    assert [t["budget_request"] for t in listed if t["id"] == task["id"]] == [asked]

    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "running", "waiting_on": ""}, headers=_as(SERVICE_KEY))
    assert api.get(f"/v1/tasks/{task['id']}", headers=_as(CALLER_KEY)).json()["budget_request"] is None


def test_a_task_with_spend_carried_asks_for_a_raise_of_its_whole_limit_and_its_workflow_gets_the_rest(api):
    """The hold's sentence counts from what the task had left when its workflow started. Read alone, it asked
    for a limit below the one the task had, which the Task API refused, so nobody could raise it."""
    from meta_harness import budget_request, db

    task = _held(api)
    with db.connect(autocommit=True) as conn:
        conn.execute("UPDATE tasks.task SET spent_carried_usd = 6.15 WHERE id = %s", (task["id"],))
    reason = budget_request.hold_reason("fix run 14", "1.14", budget_request.task_why("0.71", "3.85"))
    held = {"status": "running", "reason": reason, "waiting_on": "budget"}
    api.post(f"/v1/tasks/{task['id']}/status", json=held, headers=_as(SERVICE_KEY))

    asked = api.get(f"/v1/tasks/{task['id']}", headers=_as(CALLER_KEY)).json()["budget_request"]
    assert (asked["limit_usd"], asked["add_usd"], asked["to_usd"]) == ("10.00", "1", "11.00")
    assert asked["over_ceiling"] is False and Decimal(asked["ceiling_usd"]) >= Decimal("11")

    raise_it = {"action": "raise", "max_usd": asked["to_usd"]}
    assert api.post(f"/v1/tasks/{task['id']}/budget", json=raise_it, headers=_as(CALLER_KEY)).status_code == 202
    with db.connect(autocommit=True) as conn:
        sent = conn.execute(
            "SELECT body FROM tasks.task_message WHERE task_id = %s ORDER BY id DESC LIMIT 1", (task["id"],)
        ).fetchone()[0]
    assert sent.startswith("raise:") and Decimal(sent.partition(":")[2]) == Decimal("4.85"), (
        "the workflow counts only its own runs, so its limit is the new one less what the earlier runs spent"
    )


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


def test_a_raise_that_was_not_enough_is_read_back_with_the_holds_new_reason_and_amounts(api):
    from meta_harness import budget_request

    task = _held(api)
    answered = api.post(
        f"/v1/tasks/{task['id']}/budget", json={"action": "raise", "max_usd": "15"}, headers=_as(CALLER_KEY)
    ).json()
    # The workflow took the answer and looked again: the run still needs more than the new limit leaves.
    again = budget_request.hold_reason("fix run 4", "6", budget_request.task_why("5.20", "15"))
    api.post(
        f"/v1/tasks/{task['id']}/status",
        json={"status": "running", "reason": again, "waiting_on": "budget"},
        headers=_as(SERVICE_KEY),
    )

    read = api.get(f"/v1/tasks/{task['id']}", headers=_as(CALLER_KEY)).json()
    assert (read["status"], read["waiting_on"], read["status_reason"]) == ("running", "budget", again)
    assert (read["budget_request"]["limit_usd"], read["budget_request"]["to_usd"]) == ("15.00", "16.00")
    # What the console takes as the answer handled: the task was written after the answer was accepted.
    assert read["updated_at"] != answered["updated_at"]


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


def test_a_retry_may_ask_for_a_stronger_model_and_a_plain_retry_keeps_the_tier_the_task_has(api):
    task = _ended(api, "review_unresolved")

    stronger = api.post(f"/v1/tasks/{task['id']}/retry", json={"complexity": "high"}, headers=_as(CALLER_KEY))

    assert stronger.status_code == 202
    body = stronger.json()
    assert (body["complexity"], body["status_reason"]) == ("high", "ops retried it with a stronger model")
    unknown = api.post(f"/v1/tasks/{task['id']}/retry", json={"complexity": "huge"}, headers=_as(CALLER_KEY))
    assert unknown.status_code == 422


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
    assert merge.json()["resume_by"] == "operator"
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
    assert shown[0]["findings"] == [
        {"n": 1, "path": "a.py", "line": 3, "body": "Test it.", "dismissed": None, "id": "1.1", "outcome": None}
    ]
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
    events.emit("writing_checked", {"usd_cost": "0.02"}, task_id=tid)
    for position, event in enumerate(events.events, start=1):
        event[2]["position"] = position

    shown = api.get(f"/v1/tasks/{tid}/spend", headers=_as(CALLER_KEY)).json()

    assert Decimal(shown["max_usd"]) == Decimal("10") and Decimal(shown["totals"]["usd"]) == Decimal("1.50")
    assert [(r["kind"], r["turns"], r["usd"]) for r in shown["runs"]] == [("task", 8, "1.25"), ("review", None, "0.25")]
    assert shown["totals"]["by_kind"] == {"task": "1.25", "review": "0.25", "fix": "0"}
    assert (shown["totals"]["writing_usd"], shown["totals"]["all_usd"]) == ("0.02", "1.52")
    assert api.get("/v1/tasks/t-none/spend", headers=_as(CALLER_KEY)).status_code == 404


def test_a_smoke_task_is_a_services_to_create_and_is_left_out_of_lists_unless_asked(api):
    refused = api.post("/v1/tasks", json=_task(task_type="smoke"), headers=_as(CALLER_KEY))
    assert refused.status_code == 422 and "only Muxxy creates smoke tasks" in refused.json()["detail"]
    # As smoke.py sends it: with no acceptance check.
    created = api.post("/v1/tasks", json=_task(task_type="smoke", acceptance_checks=[]), headers=_as(SERVICE_KEY))
    assert created.status_code == 201
    smoke_id = created.json()["id"]

    listed = api.get("/v1/tasks", params={"q": smoke_id}, headers=_as(SERVICE_KEY)).json()["tasks"]
    assert listed == []
    asked = api.get("/v1/tasks", params={"q": smoke_id, "smoke": "true"}, headers=_as(SERVICE_KEY)).json()["tasks"]
    assert [t["id"] for t in asked] == [smoke_id]
    assert api.get(f"/v1/tasks/{smoke_id}", headers=_as(SERVICE_KEY)).json()["task_type"] == "smoke"


def test_only_a_plan_or_a_smoke_task_or_a_services_goal_task_may_name_no_acceptance_check(api):
    refused = api.post("/v1/tasks", json=_task(acceptance_checks=[]), headers=_as(CALLER_KEY))
    assert refused.status_code == 422
    assert "task needs at least one acceptance check" in refused.json()["detail"]
    goalless = api.post("/v1/tasks", json=_task(acceptance_checks=[]), headers=_as(SERVICE_KEY))
    assert goalless.status_code == 422, "a service's task of no goal still names its checks"
    callers = api.post("/v1/tasks", json=_task(acceptance_checks=[], goal_id="g-1"), headers=_as(CALLER_KEY))
    assert callers.status_code == 422, "a caller's goal task still names its checks"
    # As planner/sweep.py sends a goal's issue it sized, with no checks, to run with the gap shown.
    sized = api.post("/v1/tasks", json=_task(acceptance_checks=[], goal_id="g-1"), headers=_as(SERVICE_KEY))
    assert sized.status_code == 201 and sized.json()["acceptance_checks"] == []
    # As planner/planning.py sends a goal's planning task.
    plan = _task(task_type="plan", acceptance_checks=[])
    created = api.post("/v1/tasks", json=plan, headers=_as(SERVICE_KEY))
    assert created.status_code == 201 and created.json()["acceptance_checks"] == []


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


def test_each_finding_has_its_id_and_the_outcome_the_next_reviewer_or_an_operator_gave_it(api, events):
    """Nothing measured whether a reviewer's findings were right: each now says if it was fixed, not fixed,
    dismissed, or never judged."""
    task = _ended(api, "ready")
    tid = task["id"]
    findings = [{"path": "a.py", "line": n, "body": f"Finding {n}."} for n in (1, 2, 3, 4)]
    events.emit(
        "review_submitted", {"verdict": "changes", "summary": "Four.", "findings": findings}, task_id=tid, run_id="r-1"
    )
    events.emit("pull_request_reviewed", {"round": 1, "verdict": "changes", "head_sha": "abc"}, task_id=tid)
    events.emit("finding_dismissed", {"round": 1, "finding": 3, "reason": "Not ours."}, task_id=tid, actor="dave")
    events.emit("review_submitted", {"verdict": "clean", "summary": "Done.", "findings": []}, task_id=tid, run_id="r-2")
    events.emit("pull_request_reviewed", {"round": 2, "verdict": "clean", "head_sha": "def"}, task_id=tid)
    for n, outcome in ((1, "fixed"), (2, "not_fixed"), (3, "fixed")):
        events.emit("finding_resolved", {"round": 1, "finding": n, "outcome": outcome, "judged_in": 2}, task_id=tid)
    for position, event in enumerate(events.events, start=1):
        event[2]["position"] = position

    first, _ = api.get(f"/v1/tasks/{tid}/reviews", headers=_as(CALLER_KEY)).json()["rounds"]

    assert [(f["id"], f["outcome"]) for f in first["findings"]] == [
        ("1.1", "fixed"),
        ("1.2", "not_fixed"),
        ("1.3", "dismissed"),
        ("1.4", None),
    ], "an operator's dismissal stands over the reviewer's judgment; one nobody judged has none"


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


def test_an_operator_takes_a_review_over_and_gives_a_verdict_once_the_round_waits(api, events):
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    tid = task["id"]
    early = api.post(f"/v1/tasks/{tid}/review/claim", headers=_as(CALLER_KEY))
    assert early.status_code == 409 and "running task with a pull request" in early.json()["detail"]
    running = {"status": "running", "pr_url": "https://github.com/o/r/pull/9"}
    api.post(f"/v1/tasks/{tid}/status", json=running, headers=_as(SERVICE_KEY))

    claimed = api.post(f"/v1/tasks/{tid}/review/claim", headers=_as(CALLER_KEY))
    assert claimed.status_code == 202 and claimed.json()["review_claimed_by"] == "ops"
    other = {**_as(SERVICE_KEY), "X-MH-Actor": "someone-else"}
    taken = api.post(f"/v1/tasks/{tid}/review/claim", headers=other)
    assert taken.status_code == 409 and "ops already holds this review" in taken.json()["detail"]

    verdict = {"verdict": "changes", "keep": [2], "findings": [{"path": "app.py", "line": 3, "body": "Also this."}]}
    too_soon = api.post(f"/v1/tasks/{tid}/review/verdict", json=verdict, headers=_as(CALLER_KEY))
    assert too_soon.status_code == 409 and "not waiting for a verdict yet" in too_soon.json()["detail"]
    waits = {"status": "running", "reason": "waits for ops", "waiting_on": "operator_review"}
    api.post(f"/v1/tasks/{tid}/status", json=waits, headers=_as(SERVICE_KEY))
    for bad in ({"verdict": "clean", "keep": [1]}, {"verdict": "changes"}):
        assert api.post(f"/v1/tasks/{tid}/review/verdict", json=bad, headers=_as(CALLER_KEY)).status_code == 422

    given = api.post(f"/v1/tasks/{tid}/review/verdict", json=verdict, headers=_as(CALLER_KEY))
    assert given.status_code == 202 and given.json()["review_claimed_by"] is None
    pending = api.get("/v1/messages/pending", headers=_as(SERVICE_KEY)).json()["messages"]
    words = [m["body"] for m in pending if m["task_id"] == tid and m["deliver"] == "review"]
    assert words[0] == "claim:ops" and words[1].startswith('verdict:{"keep": [2], "findings": [{"body": "Also this."')
    recorded = [(t, p) for t, p, f in events.events if f.get("task_id") == tid and t.startswith("operator_review")]
    assert recorded == [
        ("operator_review_claimed", {}),
        ("operator_review_submitted", {"verdict": "changes", "kept": 1, "findings": 1}),
    ]


def test_a_claimed_review_can_be_given_back_by_the_operator_who_holds_it(api, events):
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    tid = task["id"]
    running = {"status": "running", "pr_url": "https://github.com/o/r/pull/9"}
    api.post(f"/v1/tasks/{tid}/status", json=running, headers=_as(SERVICE_KEY))
    assert api.post(f"/v1/tasks/{tid}/review/release", headers=_as(CALLER_KEY)).status_code == 409
    api.post(f"/v1/tasks/{tid}/review/claim", headers=_as(CALLER_KEY))

    released = api.post(f"/v1/tasks/{tid}/review/release", headers=_as(CALLER_KEY))

    assert released.status_code == 202 and released.json()["review_claimed_by"] is None
    pending = api.get("/v1/messages/pending", headers=_as(SERVICE_KEY)).json()["messages"]
    assert [m["body"] for m in pending if m["task_id"] == tid] == ["claim:ops", "release"]


def test_no_spend_limit_above_the_ceiling_for_one_task_is_accepted(api):
    over = api.post("/v1/tasks", json=_task(max_usd="100.01"), headers=_as(CALLER_KEY))
    assert over.status_code == 422 and "the most one task may spend" in over.json()["detail"]
    assert "$100.00" in over.json()["detail"]

    task = api.post("/v1/tasks", json=_task(max_usd="100.00"), headers=_as(CALLER_KEY)).json()
    api.post(f"/v1/tasks/{task['id']}/cancel", headers=_as(CALLER_KEY))
    retried = api.post(f"/v1/tasks/{task['id']}/retry", json={"max_usd": "5000"}, headers=_as(CALLER_KEY))
    assert retried.status_code == 422 and "max_task_usd" in retried.json()["detail"]
    raised = api.post(
        f"/v1/tasks/{task['id']}/budget", json={"action": "raise", "max_usd": "1000000"}, headers=_as(CALLER_KEY)
    )
    assert raised.status_code == 422


def test_the_supervisor_recovers_a_task_with_no_workflow_and_gives_up_after_too_many(api, events):
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    assert task["recoveries"] == 0 and task["status_changed_at"]
    running = lambda: api.post(  # noqa: E731
        f"/v1/tasks/{task['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY)
    )
    recover = lambda spent="0": api.post(  # noqa: E731
        f"/v1/tasks/{task['id']}/recover",
        json={"why": "its RunTask workflow ended without ending the task", "spent_usd": spent},
        headers=_as(SERVICE_KEY),
    )
    assert recover().status_code == 200, "a queued task the scheduler started is recovered too"
    assert api.post(f"/v1/tasks/{task['id']}/recover", json={"why": "x"}, headers=_as(CALLER_KEY)).status_code == 403
    running()

    recovered = recover("3.25").json()

    assert recovered["status"] == "queued" and recovered["recoveries"] == 2 and recovered["retries"] == 2
    assert Decimal(recovered["spent_carried_usd"]) == Decimal("3.25") and recovered["waiting_on"] is None
    assert "sent it back to the queue (recovery 2)" in recovered["status_reason"]
    assert ("task_recovered", "running", "queued") in [(e[0], e[1].get("from"), e[1].get("to")) for e in events.events]
    running()
    recover()
    running()

    gave_up = recover().json()

    assert gave_up["status"] == "failed" and gave_up["recoveries"] == 3
    assert "its workflows keep ending without finishing" in gave_up["status_reason"]
    assert recover().status_code == 409, "a failed task has no workflow to recover"
    # An operator's retry starts from nothing spent.
    retried = api.post(f"/v1/tasks/{task['id']}/retry", headers=_as(CALLER_KEY)).json()
    assert Decimal(retried["spent_carried_usd"]) == 0


def test_a_task_asked_to_cancel_is_canceled_by_a_recovery_and_a_task_says_who_has_it(api):
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
    api.post(f"/v1/tasks/{task['id']}/cancel", headers=_as(CALLER_KEY))

    ended = api.post(
        f"/v1/tasks/{task['id']}/recover", json={"why": "it has no RunTask workflow"}, headers=_as(SERVICE_KEY)
    ).json()

    assert ended["status"] == "canceled" and ended["cancel_requested"] is False
    assert ended["status_reason"] == "it was asked to cancel, and it has no RunTask workflow"
    assert api.get(f"/v1/tasks/{task['id']}", headers=_as(CALLER_KEY)).json()["standing"] is None, "not looked at yet"


def test_the_supervisor_drives_a_parked_task_a_bounded_number_of_times_with_its_spend_carried(api, events):
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()

    def park():
        api.post(f"/v1/tasks/{task['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
        body = {"status": "ready", "pr_url": "https://github.com/o/r/pull/9"}
        api.post(f"/v1/tasks/{task['id']}/status", json=body, headers=_as(SERVICE_KEY))

    def drive(action="merge", key=SERVICE_KEY):
        body = {"action": action, "why": "the code host would take its merge now", "spent_usd": "2.50"}
        return api.post(f"/v1/tasks/{task['id']}/drive", json=body, headers=_as(key))

    assert drive().status_code == 409, "a queued task is not parked"
    park()
    assert drive(key=CALLER_KEY).status_code == 403

    driven = drive().json()

    assert (driven["status"], driven["resume"], driven["drives"]) == ("queued", "merge", 1)
    assert driven["resume_by"] == "supervisor", "a merge the supervisor asks for is never taken for an operator's"
    assert Decimal(driven["spent_carried_usd"]) == Decimal("2.50")
    assert driven["status_reason"] == (
        "the code host would take its merge now, so the Task Manager asked for its merge (try 1 of 3)"
    )
    assert ("task_driven", "merge", 1) in [(e[0], e[1].get("action"), e[1].get("drives")) for e in events.events]
    park()
    assert drive("review").json()["resume"] == "review"
    park()
    assert drive().status_code == 202
    park()
    refused = drive()
    assert refused.status_code == 409 and "already sent the task back 3 times" in refused.json()["detail"]
    # An operator's own action starts the count again, and from nothing spent.
    merged = api.post(f"/v1/tasks/{task['id']}/merge", headers=_as(CALLER_KEY)).json()
    assert merged["drives"] == 0 and Decimal(merged["spent_carried_usd"]) == 0


def test_the_supervisor_starts_a_parked_task_over_with_its_tries_and_spend_counted_from_nothing(api, events):
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
    parked = {"status": "review_unresolved", "pr_url": "https://github.com/o/r/pull/9"}
    api.post(f"/v1/tasks/{task['id']}/status", json=parked, headers=_as(SERVICE_KEY))
    for _ in range(3):
        again = {"action": "review", "why": "its pull request conflicts with its base", "spent_usd": "6"}
        assert api.post(f"/v1/tasks/{task['id']}/drive", json=again, headers=_as(SERVICE_KEY)).status_code == 202
        api.post(f"/v1/tasks/{task['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
        api.post(f"/v1/tasks/{task['id']}/status", json=parked, headers=_as(SERVICE_KEY))

    over = {"action": "restart", "why": "the fix runs did not resolve its conflict", "spent_usd": "0", "counted": False}
    started = api.post(f"/v1/tasks/{task['id']}/drive", json=over, headers=_as(SERVICE_KEY))

    assert started.status_code == 202, "its three tries being used does not stop a start-over"
    body = started.json()
    assert (body["status"], body["resume"], body["resume_by"], body["drives"]) == ("queued", "restart", "supervisor", 0)
    assert Decimal(body["spent_carried_usd"]) == 0
    assert body["status_reason"].endswith("so the Task Manager started it over on the current base (once)")
    assert ("task_driven", "restart") in [(e[0], e[1].get("action")) for e in events.events]


def test_the_supervisor_retries_a_failed_task_and_only_a_failed_one(api):
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    retry = {"action": "retry", "why": "the task failed on the system's side, not its own", "spent_usd": "1"}
    assert api.post(f"/v1/tasks/{task['id']}/drive", json=retry, headers=_as(SERVICE_KEY)).status_code == 409
    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "failed"}, headers=_as(SERVICE_KEY))

    retried = api.post(f"/v1/tasks/{task['id']}/drive", json=retry, headers=_as(SERVICE_KEY)).json()

    assert (retried["status"], retried["resume"], retried["drives"]) == ("queued", None, 1)
    assert retried["status_reason"].endswith("so the Task Manager retried it (try 1 of 3)")
    assert Decimal(retried["spent_carried_usd"]) == Decimal("1")
    assert retried["complexity"] is None


def test_a_drive_can_raise_a_tasks_tier_for_its_next_runs(api):
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "failed"}, headers=_as(SERVICE_KEY))
    stronger = {"action": "retry", "why": "it failed on its own account", "complexity": "high"}

    retried = api.post(f"/v1/tasks/{task['id']}/drive", json=stronger, headers=_as(SERVICE_KEY)).json()

    assert (retried["status"], retried["complexity"]) == ("queued", "high")
    bad = {**stronger, "complexity": "extreme"}
    assert api.post(f"/v1/tasks/{task['id']}/drive", json=bad, headers=_as(SERVICE_KEY)).status_code == 422


def test_an_uncounted_drive_is_none_of_the_tasks_tries_and_tasks_are_listed_by_who_has_them(api):
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    other = api.post("/v1/tasks", json=_task(external_ref="github:o/r#778"), headers=_as(CALLER_KEY)).json()
    held = api.post("/v1/tasks", json=_task(external_ref="github:o/r#779"), headers=_as(CALLER_KEY)).json()
    for _ in range(4):
        api.post(f"/v1/tasks/{task['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
        api.post(f"/v1/tasks/{task['id']}/status", json={"status": "failed"}, headers=_as(SERVICE_KEY))
        body = {"action": "retry", "why": "the worker restarted under it", "counted": False}
        driven = api.post(f"/v1/tasks/{task['id']}/drive", json=body, headers=_as(SERVICE_KEY))
        assert driven.status_code == 202 and driven.json()["drives"] == 0
    assert driven.json()["status_reason"].endswith("(not one of its tries)")

    from meta_harness import db
    from meta_harness.supervisor import standing
    from meta_harness.supervisor.rules import Decision

    with db.connect(autocommit=True) as conn:
        standing.save(conn, task["id"], Decision("operator", "failed", "the task failed", ask="Retry it."))
        standing.save(conn, other["id"], Decision("scheduler", "queued", "it waits for the scheduler"))
        paused = Decision("operator", "goal_not_active", "its goal is paused", ask="Resume the goal.")
        standing.save(conn, held["id"], paused)

    yours = api.get("/v1/tasks?has_it=operator", headers=_as(CALLER_KEY)).json()

    assert [t["id"] for t in yours["tasks"]] == [task["id"]] and yours["total"] == 1, (
        "a paused goal's tasks are its own"
    )
    assert yours["tasks"][0]["standing"]["ask"] == "Retry it."
    assert api.get("/v1/tasks?has_it=scheduler", headers=_as(CALLER_KEY)).json()["total"] == 1
    assert api.get("/v1/tasks?has_it=nobody", headers=_as(CALLER_KEY)).status_code == 422


def test_an_ended_task_with_no_pull_request_is_made_over_from_what_its_issue_says_now(api) -> None:
    """An issue is one task, by its ref. An issue edited after its first task was canceled, or taken in by
    another goal, must not run again from the text it had the first time."""
    ref = f"jira:VL-{uuid.uuid4().int % 10**9}"
    first = api.post(
        "/v1/tasks",
        json=_task(external_ref=ref, title="Placeholder", description="Placeholder"),
        headers=_as(SERVICE_KEY),
    )
    task = first.json()
    assert first.status_code == 201

    # While it has not ended, the same issue submitted again changes nothing.
    again = _task(
        external_ref=ref,
        title="Retry all states its total",
        description="Make the button ask first.",
        acceptance_checks=["npm test"],
    )
    same = api.post("/v1/tasks", json=again, headers=_as(SERVICE_KEY)).json()
    assert (same["id"], same["title"], same["status"]) == (task["id"], "Placeholder", "queued")

    assert (
        api.post(
            f"/v1/tasks/{task['id']}/cancel", json={"reason": "started too early"}, headers=_as(SERVICE_KEY)
        ).status_code
        == 200
    )
    renewed = api.post("/v1/tasks", json=again, headers=_as(SERVICE_KEY))

    made = renewed.json()
    assert renewed.status_code == 200 and made["id"] == task["id"], "the same task, not a second one for the issue"
    assert (made["status"], made["title"], made["description"]) == (
        "queued",
        "Retry all states its total",
        "Make the button ask first.",
    )
    assert made["acceptance_checks"] == ["npm test"] and made["retries"] == task["retries"] + 1
    assert "remade it from its issue's current text" in made["status_reason"]

    # One with a pull request keeps it and resumes at its review: the workflow starts over only if it was closed.
    api.post(
        f"/v1/tasks/{task['id']}/status",
        json={"status": "running", "pr_url": "https://github.com/o/r/pull/9"},
        headers=_as(SERVICE_KEY),
    )
    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "failed"}, headers=_as(SERVICE_KEY))
    kept = api.post("/v1/tasks", json={**again, "title": "Third wording"}, headers=_as(SERVICE_KEY)).json()
    assert (kept["status"], kept["title"], kept["resume"], kept["pr_url"]) == (
        "queued",
        "Third wording",
        "review",
        "https://github.com/o/r/pull/9",
    )

    # A caller that is not a control-plane service gets its existing task back as it is.
    mine = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    api.post(f"/v1/tasks/{mine['id']}/cancel", json={"reason": "x"}, headers=_as(CALLER_KEY))
    untouched = api.post(
        "/v1/tasks", json=_task(external_ref=mine["external_ref"], title="Other words"), headers=_as(CALLER_KEY)
    ).json()
    assert (untouched["status"], untouched["title"]) == ("canceled", mine["title"])


def test_a_task_with_a_pull_request_is_started_over_and_one_without_is_told_to_retry(api):
    task = _ended(api, "review_unresolved")

    restarted = api.post(f"/v1/tasks/{task['id']}/restart", json={"max_usd": "20"}, headers=_as(CALLER_KEY))

    assert restarted.status_code == 202
    body = restarted.json()
    assert (body["status"], body["resume"], body["retries"]) == ("queued", "restart", 1)
    assert body["status_reason"] == "ops started it over on the current base"

    bare = api.post("/v1/tasks", json=_task(external_ref="github:o/r#901"), headers=_as(CALLER_KEY)).json()
    api.post(f"/v1/tasks/{bare['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
    api.post(f"/v1/tasks/{bare['id']}/status", json={"status": "failed"}, headers=_as(SERVICE_KEY))
    refused = api.post(f"/v1/tasks/{bare['id']}/restart", headers=_as(CALLER_KEY))
    assert refused.status_code == 409 and "retry it instead" in refused.json()["detail"]


def test_a_message_for_a_run_that_ended_is_dropped_and_never_reaches_a_later_run(api, events):
    """A task's workflow keeps its id when the task is retried, so a "stop" written for a run that then ended
    would be handed to the next run of the same task."""
    task = api.post("/v1/tasks", json=_task(), headers=_as(CALLER_KEY)).json()
    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "running"}, headers=_as(SERVICE_KEY))
    assert (
        api.post(f"/v1/tasks/{task['id']}/messages", json={"when": "stop"}, headers=_as(CALLER_KEY)).status_code == 202
    )

    def pending() -> list[dict]:
        found = api.get("/v1/messages/pending", headers=_as(SERVICE_KEY)).json()["messages"]
        return [m for m in found if m["task_id"] == task["id"]]

    # The run ends before the message is handed over, and the task is started again.
    api.post(f"/v1/tasks/{task['id']}/status", json={"status": "failed"}, headers=_as(SERVICE_KEY))
    assert pending() == [], "a task that is not running has no run to hand it to"
    with db.connect(autocommit=True) as conn:
        conn.execute("UPDATE tasks.task SET status = 'running', status_changed_at = now() WHERE id = %s", (task["id"],))
        dropped = conn.execute(
            "SELECT dropped_at IS NOT NULL, delivered_at FROM tasks.task_message WHERE task_id = %s", (task["id"],)
        ).fetchone()
    assert dropped == (True, None), "dropped, and not marked as delivered"
    assert pending() == [], "the later run never gets it"

    # A message written for the run that is on now is handed over as before.
    assert (
        api.post(f"/v1/tasks/{task['id']}/messages", json={"text": "Now."}, headers=_as(CALLER_KEY)).status_code == 202
    )
    assert [m["body"] for m in pending()] == ["Now."]


def test_the_reviewers_table_reads_every_tasks_review_events_within_the_days_asked(api, events):
    from datetime import datetime, timezone

    tid = _ended(api, "ready")["id"]
    now = datetime.now(timezone.utc).isoformat()
    reviewer = "harness:pi-review/claude:claude-opus-5-5"
    events.emit(
        "review_submitted",
        {"verdict": "changes", "summary": "One.", "findings": [{"path": None, "line": None, "body": "Fix."}]},
        task_id=tid,
        time_utc=now,
    )
    events.emit(
        "pull_request_reviewed", {"round": 1, "verdict": "changes", "reviewer": reviewer}, task_id=tid, time_utc=now
    )
    for position, event in enumerate(events.events, start=1):
        event[2]["position"] = position

    answer = api.get("/v1/reviewers", params={"days": 7}, headers=_as(CALLER_KEY))

    assert answer.status_code == 200
    [row] = [r for r in answer.json()["reviewers"] if r["reviewer"] == reviewer]
    assert (row["rounds"], row["findings"], row["never_judged"]) == (1, 1, 1)
    assert api.get("/v1/reviewers", params={"days": 0}, headers=_as(CALLER_KEY)).status_code == 422
