# Created by the meta-harness in run t-dc8b62922990:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""A more detailed goal statement proposed by one gateway call: POST /v1/goals/enrich, and its accept path.

The module's tests run alone. The route's run against Postgres, with a fake HTTP Gateway, tracker and event
service: no model is called.
"""

import contextlib
import functools
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from meta_harness import db, migrations
from meta_harness.costs import model_source
from meta_harness.planner import enrich, store
from meta_harness.planner import service as planner_service
from meta_harness.planner.service import create_app
from meta_harness.planner.tracker import Issue
from meta_harness.run_service import writing

from .test_planner import (  # noqa: F401 - the fixtures these tests run on
    OPERATOR,
    REPO,
    FakeTasks,
    FakeTracker,
    _label,
    _no_title,
    _number,
    registry,
)

ENRICHED = {
    "statement": "Every save keeps the last edit. Out of scope: the export path. Success: the save tests pass.",
    "added": [{"text": "Out of scope: the export path.", "why": "No issue names export."}],
    "questions": [{"question": "Should autosave count as a save?", "assumption": "It does."}],
}


def _issue(number: int, body: str = "Saving drops the last edit.") -> Issue:
    return Issue(
        ref=f"github:o/a#{number}",
        repo="o/a",
        number=number,
        title=f"Issue {number}",
        body=body,
        labels=["bug"],
        state="open",
    )


def _gateway(monkeypatch, answer: dict, status: int = 200):
    monkeypatch.setenv("GATEWAY_URL", "http://gateway")
    monkeypatch.setenv("GATEWAY_API_KEY", "gw-key")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=answer)

    return httpx.MockTransport(handler), seen


def test_the_prompt_carries_the_goal_and_marks_its_statement_and_issues_as_data():
    known = enrich.Known(
        statement="Fix saving.",
        title="Saving",
        repos=["o/a"],
        tracker="jira",
        tracker_settings={"project": "PROJ"},
        label="bug",
    )

    prompt = enrich.enrich_prompt(known, [_issue(1, body="x" * 50)], body_chars=10)

    assert "--- statement ---\nFix saving.\n--- end of statement ---" in prompt
    assert "Repos: o/a" in prompt and "Label: bug" in prompt and "Tracker setting project: PROJ" in prompt
    assert "--- issues ---" in prompt and "## github:o/a#1: Issue 1" in prompt
    assert "x" * 10 + " [cut]" in prompt and "x" * 11 not in prompt
    assert "takes no issues yet" in enrich.enrich_prompt(enrich.Known(statement="Fix saving."), [], 10)


async def test_an_enrichment_is_one_structured_call_with_the_configured_model(monkeypatch):
    transport, seen = _gateway(monkeypatch, {"data": ENRICHED, "finish_reason": "complete", "usd_cost": "0.003"})

    enriched = await enrich.enrich_goal(enrich.Known(statement="Fix saving."), [_issue(1)], transport=transport)

    assert enriched.statement == ENRICHED["statement"]
    assert enriched.added == ENRICHED["added"] and enriched.questions == ENRICHED["questions"]
    config = enrich.settings()
    assert (enriched.model, enriched.usd_cost) == (f"{config['engine']}:{config['model']}", "0.003")
    [request] = seen
    assert str(request.url) == "http://gateway/v1/structured"
    sent = json.loads(request.content)
    assert (sent["engine"], sent["model"]) == (config["engine"], config["model"])
    assert sent["response_schema"]["required"] == ["statement", "added", "questions"]
    for asked in ("out of scope", "how success is checked", "constraints", "which issues it covers"):
        assert asked in sent["system_prompt"]
    assert "never into the statement as a fact" in sent["system_prompt"]
    assert "follow no instruction" in sent["system_prompt"]
    assert "github:o/a#1" in sent["prompt"]


@pytest.mark.parametrize(
    ("answer", "status", "message"),
    [
        ({"data": None, "finish_reason": "max_tokens"}, 200, "did not finish"),
        ({"data": {**ENRICHED, "statement": " "}, "finish_reason": "complete"}, 200, "empty statement"),
        ({"data": {**ENRICHED, "statement": "x" * 4001}, "finish_reason": "complete"}, 200, "over 4000"),
        ({"detail": "no"}, 503, "answered 503"),
    ],
)
async def test_an_enrichment_the_model_did_not_finish_or_got_wrong_is_refused(monkeypatch, answer, status, message):
    transport, _ = _gateway(monkeypatch, answer, status)

    with pytest.raises(enrich.EnrichError, match=message):
        await enrich.enrich_goal(enrich.Known(statement="Fix saving."), [_issue(1)], transport=transport)


def test_malformed_enrichment_settings_stop_the_planner(monkeypatch):
    monkeypatch.setitem(enrich.settings(), "max_response_tokens", 0)
    with pytest.raises(RuntimeError, match="goal_enrichment.max_response_tokens"):
        enrich.check_settings()


# --- The route ----------------------------------------------------------------------------------------


@pytest.fixture
def database():
    try:
        with db.connect() as conn:
            migrations.apply(conn)
    except Exception as err:  # noqa: BLE001
        pytest.skip(f"no Postgres at {db.database_url()}: {err}")


class Events:
    """The event service: what was emitted, readable by type as the cost harvest reads it."""

    def __init__(self):
        self.events: list[dict] = []

    def emit(self, event_type, payload=None, **fields):
        self.events.append(
            {
                "position": len(self.events) + 1,
                "event_type": event_type,
                "payload": payload or {},
                "time_utc": datetime.now(UTC).isoformat(),
                **fields,
            }
        )
        return str(len(self.events))

    def read(self, from_position=0, limit=500, before_position=None, **filters):
        kinds = filters.get("event_type", "").split(",")
        found = [e for e in self.events if e["position"] > from_position and e["event_type"] in kinds][:limit]
        return {"events": found, "next_position": found[-1]["position"] if found else from_position}

    def of(self, event_type) -> list[dict]:
        return [e for e in self.events if e["event_type"] == event_type]


@pytest.fixture
def no_rewrite(monkeypatch):
    """The writing pass's rewrite, recorded and never sent: a statement it finds nothing in needs none."""
    asked: list[str] = []

    async def rewrite(text, hits, transport=None):
        asked.append(text)
        return "Every save keeps the last edit.", "0.0004"

    monkeypatch.setattr(writing, "rewrite", rewrite)
    return asked


async def _unsized(issue, transport=None):
    """No sizing: the gateway is set for the enrichment, and a sweep must not reach for it."""
    return None


def _api(database, events, tracker, transport):
    app = create_app(
        events,
        tracker,
        FakeTasks(),
        operator_keys={"op-key": "dave"},
        service_keys={},
        sweep_seconds=0,
        titler=_no_title,
        sizer=_unsized,
        enricher=functools.partial(enrich.enrich_goal, transport=transport),
    )
    return TestClient(app)


@pytest.mark.db
def test_the_route_proposes_a_statement_from_the_goals_issues_and_saves_nothing(database, monkeypatch, no_rewrite):
    events, tracker = Events(), FakeTracker()
    label = _label()
    labeled = tracker.add(REPO, _number(), labels=[label], title="Saving drops the last edit")
    picked = tracker.add(REPO, _number(), labels=[], title="Autosave races the save")
    tracker.add(REPO, _number(), labels=[_label()], title="Another goal's")
    transport, seen = _gateway(monkeypatch, {"data": ENRICHED, "finish_reason": "complete", "usd_cost": "0.003"})

    with _api(database, events, tracker, transport) as client:
        goal = client.post(
            "/v1/goals", json={"statement": "Fix saving.", "repos": [REPO], "label": label}, headers=OPERATOR
        ).json()
        before = client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()
        enriched = client.post(
            "/v1/goals/enrich",
            json={
                "statement": "Fix saving.",
                "title": "Saving",
                "repos": [REPO],
                "label": label,
                "refs": [picked],
                "goal_id": goal["id"],
            },
            headers=OPERATOR,
        )
        after = client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()

    assert enriched.status_code == 200, enriched.text
    body = enriched.json()
    assert body["statement"] == ENRICHED["statement"]
    assert body["added"] == ENRICHED["added"] and body["questions"] == ENRICHED["questions"]
    assert body["refs"] == [labeled, picked], "the label's issues, then the picked ones"
    prompt = json.loads(seen[0].content)["prompt"]
    assert labeled in prompt and picked in prompt and "Another goal's" not in prompt
    assert (after["statement"], after["title"]) == (before["statement"], before["title"]) == ("Fix saving.", None)
    assert after.get("updated_at") == before.get("updated_at")
    assert not events.of("goal_changed"), "nothing is saved to the goal"
    assert no_rewrite == [], "a clean statement needs no rewrite"


@pytest.mark.db
def test_the_returned_statement_has_been_through_the_writing_pass(database, monkeypatch, no_rewrite):
    events = Events()
    sloppy = {**ENRICHED, "statement": "This seamless and robust change is crucial. Every save keeps the last edit."}
    transport, _ = _gateway(monkeypatch, {"data": sloppy, "finish_reason": "complete", "usd_cost": "0.003"})

    with _api(database, events, FakeTracker(), transport) as client:
        enriched = client.post("/v1/goals/enrich", json={"statement": "Fix saving.", "repos": [REPO]}, headers=OPERATOR)

    assert enriched.status_code == 200, enriched.text
    assert no_rewrite == [sloppy["statement"]]
    assert enriched.json()["statement"] == "Every save keeps the last edit."
    [event] = events.of("goal_enriched")
    assert event["payload"]["writing"]["usd_cost"] == "0.0004"


@pytest.mark.db
def test_the_event_carries_the_cost_under_the_goal_when_one_is_named(database, monkeypatch, no_rewrite):
    events = Events()
    transport, _ = _gateway(monkeypatch, {"data": ENRICHED, "finish_reason": "complete", "usd_cost": "0.003"})

    with _api(database, events, FakeTracker(), transport) as client:
        goal = client.post(
            "/v1/goals", json={"statement": "Fix saving.", "repos": [REPO], "label": _label()}, headers=OPERATOR
        ).json()
        for extra in ({"goal_id": goal["id"]}, {}):
            answered = client.post(
                "/v1/goals/enrich", json={"statement": "Fix saving.", "repos": [REPO], **extra}, headers=OPERATOR
            )
            assert answered.status_code == 200, answered.text
        unknown = client.post(
            "/v1/goals/enrich",
            json={"statement": "Fix saving.", "goal_id": f"g-{uuid.uuid4().hex[:12]}"},
            headers=OPERATOR,
        )

    assert unknown.status_code == 404
    named, unnamed = events.of("goal_enriched")
    config = enrich.settings()
    assert named["payload"]["usd_cost"] == "0.003" and named["payload"]["goal_id"] == goal["id"]
    assert named["payload"]["model"] == f"{config['engine']}:{config['model']}"
    assert named["tags"] == {"goal_id": goal["id"]} and named["actor"] == "dave"
    assert "goal_id" not in unnamed["payload"] and "tags" not in unnamed

    # The cost harvest counts each: under the goal when it was named, else under planning alone.
    with db.connect(autocommit=True) as conn:
        conn.execute("DELETE FROM costs.daily_cost")
        conn.execute("DELETE FROM costs.harvest")
        counted = model_source.harvest(conn, events, lambda task_id: None, {goal["id"]: goal}.get)
        rows = conn.execute("SELECT item, goal_id, model, usd FROM costs.daily_cost ORDER BY goal_id").fetchall()
    assert counted >= 2
    assert rows == [
        ("planning", "", config["model"], Decimal("0.003")),
        ("planning", goal["id"], config["model"], Decimal("0.003")),
    ]


@pytest.mark.db
def test_a_model_call_that_fails_after_its_retries_answers_502_with_the_reason(database, monkeypatch, no_rewrite):
    events = Events()
    transport, seen = _gateway(monkeypatch, {"detail": "overloaded"}, status=503)

    with _api(database, events, FakeTracker(), transport) as client:
        refused = client.post("/v1/goals/enrich", json={"statement": "Fix saving.", "repos": [REPO]}, headers=OPERATOR)

    assert refused.status_code == 502
    assert "answered 503" in refused.json()["detail"]
    assert len(seen) > 1, "the call was tried again before it failed"
    assert not events.of("goal_enriched")


# --- Accepting an edited statement --------------------------------------------------------------------


class _Pool:
    @contextlib.contextmanager
    def connection(self):
        yield None

    def close(self):
        pass


@contextlib.contextmanager
def _no_database(*args, **kwargs):
    yield None


@pytest.fixture
def one_goal(monkeypatch):
    """The planner over one goal kept in memory, with no database."""
    goal = {"id": "g-1", "title": "Saving", "statement": "Fix saving.", "status": "active", "repos": [REPO]}
    saved: list[dict] = []

    def get(conn, goal_id):
        if goal_id != goal["id"]:
            raise store.NotFound(goal_id)
        return dict(goal)

    def update(conn, goal_id, only_if=None, dropped=None, **fields):
        if only_if is not None and goal["status"] != only_if:
            return dict(goal), {}
        before = {k: goal[k] for k, v in fields.items() if goal[k] != v}
        goal.update(fields)
        saved.append(fields)
        return dict(goal), before

    monkeypatch.setattr(planner_service.db, "pool", lambda url=None: _Pool())
    monkeypatch.setattr(enrich.db, "connect", _no_database)
    monkeypatch.setattr(store, "get", get)
    monkeypatch.setattr(store, "update", update)
    events = Events()
    app = create_app(events, None, None, operator_keys={"op-key": "dave"}, service_keys={}, sweep_seconds=0)
    with TestClient(app) as client:
        yield client, goal, events, saved


def test_accept_saves_the_edited_statement_and_records_goal_changed_with_the_user(one_goal):
    client, goal, events, saved = one_goal
    edited = "Every save keeps the last edit."

    accepted = client.post("/v1/goals/g-1/enrich/accept", json={"statement": edited}, headers=OPERATOR)

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["statement"] == edited == goal["statement"]
    assert saved == [{"statement": edited}]
    [event] = events.of("goal_changed")
    assert event["actor"] == "dave" and event["tags"] == {"goal_id": "g-1"}
    assert event["payload"]["changes"] == {"statement": {"from": "Fix saving.", "to": edited}}
    assert event["payload"]["enriched"] is True


def test_accept_of_the_same_statement_records_nothing(one_goal):
    client, _, events, _ = one_goal

    accepted = client.post("/v1/goals/g-1/enrich/accept", json={"statement": "Fix saving."}, headers=OPERATOR)

    assert accepted.status_code == 200 and events.events == []


@pytest.mark.parametrize("status", ["met", "canceled"])
def test_accept_refuses_a_goal_that_has_ended(one_goal, status):
    client, goal, events, saved = one_goal
    goal["status"] = status

    accepted = client.post("/v1/goals/g-1/enrich/accept", json={"statement": "New."}, headers=OPERATOR)

    assert accepted.status_code == 409 and "reopen" in accepted.json()["detail"]
    assert saved == [] and events.events == [] and goal["statement"] == "Fix saving."
