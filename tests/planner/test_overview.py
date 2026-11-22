# Created by the meta-harness in run t-45094842390c:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""The planner's Overview report: one report on how all running goals are going, written only when they change.

The planner's API runs against Postgres with fakes for the tracker, the Task API and the event service. The
model is a mock HTTP Gateway: no model is called. The writing pass is a fake that keeps each text.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from meta_harness import db
from meta_harness.costs import model_source
from meta_harness.planner import overview, planning, store
from meta_harness.planner.service import create_app
from meta_harness.run_service import writing

from .test_planner import OPERATOR, Events, FakeTasks, _no_title, migrated, registry  # noqa: F401
from .test_planning import WritingTracker, _feature

SINCE = {"since": "2020-01-01T00:00:00Z"}


class Gateway:
    """A mock HTTP Gateway behind the real model call: keeps each request and answers with one report."""

    def __init__(self):
        self.requests: list[dict] = []
        self.goal_id = ""

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        data = {
            "headline": "One goal waits on you.",
            "moving": "Both goals are being planned.",
            "needs_you": [
                {"goal_id": self.goal_id, "what": "Approve the plan.", "why": "Nothing runs until you do."},
                {"goal_id": "g-not-a-goal", "what": "Look at it.", "why": "It said so."},
            ],
            "spend": "Neither goal has spent anything yet.",
            "finished": "",
        }
        return httpx.Response(200, json={"data": data, "finish_reason": "complete", "usd_cost": "0.004"})

    async def __call__(self, found: dict, before: dict | None) -> overview.Written:
        return await overview.write_report(found, before, httpx.MockTransport(self.handle))

    def facts(self, n: int = -1) -> dict:
        prompt = self.requests[n]["prompt"]
        return json.loads(prompt.split("--- facts ---\n", 1)[1].split("\n--- end of facts ---", 1)[0])


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", "http://gateway.test")
    monkeypatch.setenv("GATEWAY_API_KEY", "k")
    checked: list[str] = []

    async def check_all(texts, transport=None):
        checked.extend(texts)
        return [writing.Outcome(text=t) for t in texts]

    monkeypatch.setattr(writing, "check_all", check_all)
    # Only this test's goals are running: the database holds other tests' goals too.
    mine: set[str] = set()
    every = store.list_goals
    monkeypatch.setattr(
        store, "list_goals", lambda conn, statuses=None: [g for g in every(conn, statuses) if g["id"] in mine]
    )
    events, tasks, gateway = Events(), FakeTasks(), Gateway()
    app = create_app(
        events,
        WritingTracker(),
        tasks,
        operator_keys={"op-key": "dave"},
        service_keys={},
        sweep_seconds=0,
        titler=_no_title,
        overview_writer=gateway,
    )
    with TestClient(app) as client:
        yield client, events, gateway, mine, checked


def _goals(client, mine: set[str], n: int) -> list[dict]:
    goals = [_feature(client) for _ in range(n)]
    mine.update(goal["id"] for goal in goals)
    return goals


def _overview(monkeypatch, **changes) -> None:
    shipped = planning.settings()
    monkeypatch.setattr(planning, "settings", lambda: {**shipped, "overview": {**shipped["overview"], **changes}})


def _report(client, **params) -> dict:
    answer = client.get("/v1/overview/report", params=params, headers=OPERATOR)
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_two_goals_facts_give_one_report_and_an_unchanged_digest_writes_none(world):
    client, events, gateway, mine, checked = world
    first, second = _goals(client, mine, 2)
    gateway.goal_id = first["id"]
    planning_task = next(
        p["task_id"] for t, p in events.events if t == "plan_requested" and p["goal_id"] == first["id"]
    )
    events.emit("task_status_changed", {"from": "queued", "to": "running"}, task_id=planning_task)

    assert _report(client)["report"] is None, "without since, nothing is written"
    shown = _report(client, **SINCE)

    assert shown["written"] is True and len(gateway.requests) == 1, "one model call for both goals"
    facts = {goal["id"]: goal for goal in gateway.facts()["goals"]}
    assert set(facts) == {first["id"], second["id"]}
    assert all(goal["status"] == "planning" for goal in facts.values())
    assert [r["detail"] for r in facts[first["id"]]["recorded_since_last_report"]] == ["queued to running"]
    assert facts[second["id"]]["recorded_since_last_report"] == []
    report = shown["report"]
    assert (report["headline"], report["goals"], report["written_at"]) == (
        "One goal waits on you.",
        2,
        shown["written_at"],
    )
    assert [need["goal_id"] for need in report["needs_you"]] == [first["id"], ""], "a goal the facts lack is dropped"
    assert {"One goal waits on you.", "Both goals are being planned.", "Nothing runs until you do."} <= set(checked)
    assert "say only what the facts say" in gateway.requests[0]["system_prompt"].lower()
    assert f"{gateway.requests[0]['engine']}:{gateway.requests[0]['model']}" == overview.settings()["model"]

    again = _report(client, **SINCE)

    assert again["written"] is False and again["report"] == report, "nothing changed: the latest stays"
    assert len(gateway.requests) == 1, "an unchanged digest makes no model call"
    assert [t for t in events.types() if t == overview.EVENT] == [overview.EVENT]
    assert _report(client)["report"] == report


def test_past_max_usd_no_model_call_is_made_and_the_last_report_is_returned(world, monkeypatch):
    client, events, gateway, mine, _ = world
    _overview(monkeypatch, max_usd="0.004", min_seconds=1)
    _goals(client, mine, 2)
    _report(client, **SINCE)
    # The report is older than min_seconds, and a third goal starts: the facts changed.
    written = client.app.state.overview.latest()
    written["written_at"] = (datetime.now(UTC) - timedelta(seconds=2)).isoformat()
    _goals(client, mine, 1)

    held = _report(client, **SINCE)

    assert len(gateway.requests) == 1, "the day's max_usd is spent: no model call"
    assert held["written"] is False and held["report"] == written
    assert held["written_at"] == written["written_at"]
    assert "max_usd" in held["held"] and written["written_at"] in held["held"], "it says when it was written"

    _overview(monkeypatch, max_usd="1.00", min_seconds=1)
    assert _report(client, **SINCE)["written"] is True and len(gateway.requests) == 2


def test_the_event_carries_the_cost_and_the_cost_harvest_counts_it_as_planning(world):
    client, events, gateway, mine, _ = world
    _goals(client, mine, 2)
    _report(client, **SINCE)

    [payload] = [p for t, p in events.events if t == overview.EVENT]
    assert (payload["model"], payload["usd_cost"]) == (overview.settings()["model"], "0.004")

    class Recorded:
        def read(self, from_position=0, limit=500, **filters):
            wanted = filters.get("event_type", "").split(",")
            found = [
                {**e, "time_utc": "2026-11-11T12:00:00+00:00"}
                for e in events.stored.values()
                if e["position"] > from_position and e["event_type"] in wanted
            ]
            return {"events": found[:limit]}

    with db.connect(autocommit=True) as conn:
        conn.execute("DELETE FROM costs.daily_cost")
        conn.execute("DELETE FROM costs.harvest")
        model_source.harvest(conn, Recorded(), lambda _: None, lambda _: None)
        rows = conn.execute("SELECT item, goal_id, model, usd FROM costs.daily_cost").fetchall()
    assert rows == [("planning", "", "claude-haiku-4-5", Decimal("0.004"))]


def test_the_reports_are_read_back_from_their_events_after_a_restart(world):
    client, events, gateway, mine, _ = world
    _goals(client, mine, 1)
    written = _report(client, **SINCE)["report"]

    reports = overview.Reports(events)

    assert reports.load()[-1] == written and reports.spent_today() == Decimal("0.004")


def test_a_bad_overview_setting_is_named_at_startup(monkeypatch):
    _overview(monkeypatch, max_usd="0", min_seconds=0)

    with pytest.raises(RuntimeError, match="overview.max_usd") as raised:
        overview.check_settings()
    assert "overview.min_seconds" in str(raised.value)
