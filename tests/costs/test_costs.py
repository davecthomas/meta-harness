"""Costs gathered from their sources into one place, and served as a time series with a trend.

The series math runs alone. The harvests and the API run against Postgres with fakes for the event service,
the Task API and BigQuery: no model, no Google Cloud.
"""

import uuid
from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from meta_harness import db, migrations
from meta_harness.costs import api as costs_api
from meta_harness.costs import cloud_source, model_source, series, store
from meta_harness.planner.service import create_app

OPERATOR = {"Authorization": "Bearer op-key"}
SERVICE = {"Authorization": "Bearer svc-key"}


def test_days_fall_into_buckets_of_a_day_a_week_or_a_month_with_none_left_out():
    assert series.bucket_start(date(2026, 11, 11), "week") == date(2026, 11, 9)
    assert series.bucket_start(date(2026, 11, 11), "month") == date(2026, 11, 1)
    assert series.buckets(date(2026, 11, 9), date(2026, 11, 11), "day") == [
        date(2026, 11, 9),
        date(2026, 11, 10),
        date(2026, 11, 11),
    ]
    assert series.buckets(date(2026, 10, 30), date(2026, 12, 2), "month") == [
        date(2026, 10, 1),
        date(2026, 11, 1),
        date(2026, 12, 1),
    ]


def test_the_trend_is_the_least_squares_line_through_the_buckets_totals():
    assert series.trend([1.0, 2.0, 3.0, 4.0]) == {"slope": 1.0, "start": 1.0, "end": 4.0}
    flat = series.trend([5.0, 5.0, 5.0])
    assert flat["slope"] == 0 and flat["start"] == flat["end"] == 5.0
    assert series.trend([7.0]) is None, "one bucket has no trend"


def test_a_series_per_value_dearest_first_with_the_rest_folded_into_other():
    rows = [(date(2026, 11, 9), f"m{n}", Decimal(n + 1)) for n in range(9)]
    rows.append((date(2026, 11, 10), "m8", Decimal("0.5")))
    built = series.build(rows, date(2026, 11, 9), date(2026, 11, 10), "day")
    assert built["buckets"] == ["2026-11-09", "2026-11-10"]
    assert [s["key"] for s in built["series"]] == ["m8", "m7", "m6", "m5", "m4", "m3", "m2", "other"]
    assert built["series"][0]["usd"] == [9.0, 0.5] and built["series"][-1]["usd"] == [3.0, 0.0]
    assert built["totals"] == [45.0, 0.5]


@pytest.fixture(scope="module", autouse=True)
def migrated():
    try:
        with db.connect() as conn:
            migrations.apply(conn)
    except Exception as err:  # noqa: BLE001
        pytest.skip(f"no Postgres at {db.database_url()}: {err}")


@pytest.fixture
def clean():
    with db.connect(autocommit=True) as conn:
        conn.execute("DELETE FROM costs.daily_cost")
        conn.execute("DELETE FROM costs.harvest")


class Events:
    def __init__(self):
        self.events: list[dict] = []

    def emit(self, event_type, payload=None, **fields):
        return self.add(event_type, payload or {}, **fields)

    def add(self, event_type, payload, day="2026-11-10", **fields):
        event = {
            "position": len(self.events) + 1,
            "event_type": event_type,
            "payload": payload,
            "time_utc": f"{day}T12:00:00+00:00",
            **fields,
        }
        self.events.append(event)
        return str(event["position"])

    def read(self, from_position=0, limit=500, before_position=None, **filters):
        kinds = filters.get("event_type", "").split(",")
        found = [e for e in self.events if e["position"] > from_position and e["event_type"] in kinds][:limit]
        return {"events": found}


class Tasks:
    def __init__(self, tasks: dict[str, dict]):
        self.tasks = tasks

    def get(self, task_id):
        return self.tasks[task_id]

    def list(self, statuses=None, goal_id=None, limit=100):
        return []

    def page(self, statuses=None, goal_id=None, limit=25, offset=0):
        return {"tasks": [], "total": 0, "offset": offset, "limit": limit}


TASKS = {
    "t-1": {"id": "t-1", "title": "Fix it", "goal_id": "g-a", "repo": "o/r", "task_type": "bug", "caller": "planner"},
    "t-2": {"id": "t-2", "title": "Plan", "goal_id": "g-b", "repo": "o/r", "task_type": "plan", "caller": "planner"},
}
GOALS = {
    "g-a": {"id": "g-a", "created_by": "dave@example.com", "title": "Saves", "statement": "x", "repos": ["o/r"]},
    "g-b": {"id": "g-b", "created_by": "pat@example.com", "title": "Exports", "statement": "x", "repos": ["o/r"]},
}


def _events() -> Events:
    events = Events()
    sonnet, opus = "harness:pi-lean/claude:claude-sonnet-5", "harness:pi-review/claude:claude-opus-5-5"
    events.add("attempt_finished", {"spent_usd": "2.50", "candidate": sonnet, "tier": "standard"}, task_id="t-1")
    events.add("attempt_finished", {"spent_usd": "0.75", "candidate": opus, "tier": "review"}, task_id="t-1")
    events.add("attempt_finished", {"spent_usd": "1.25", "candidate": sonnet}, "2026-11-11", task_id="t-2")
    events.add("goal_titled", {"goal_id": "g-a", "model": "claude:claude-haiku-5", "usd_cost": "0.01"}, "2026-11-11")
    return events


def _harvest(events: Events) -> int:
    with db.connect(autocommit=True) as conn:
        return model_source.harvest(conn, events, TASKS.get, GOALS.get)


def test_model_cost_is_harvested_once_from_the_events_that_record_it(clean):
    events = _events()
    assert _harvest(events) == 4
    assert _harvest(events) == 0, "what was counted is not counted again"
    events.add("attempt_finished", {"spent_usd": "1", "candidate": "x/claude:claude-sonnet-5"}, task_id="t-1")
    assert _harvest(events) == 1

    with db.connect(autocommit=True) as conn:
        rows = conn.execute(
            "SELECT day::text, item, goal_id, task_id, model, engine, operator, usd FROM costs.daily_cost "
            "ORDER BY day, item, goal_id"
        ).fetchall()
    assert rows == [
        ("2026-11-10", "review", "g-a", "t-1", "claude-opus-5-5", "claude", "dave@example.com", Decimal("0.75")),
        # The task's own two runs on one day and model are one row.
        ("2026-11-10", "task", "g-a", "t-1", "claude-sonnet-5", "claude", "dave@example.com", Decimal("3.50")),
        ("2026-11-11", "planning", "g-a", "", "claude-haiku-5", "claude", "dave@example.com", Decimal("0.01")),
        ("2026-11-11", "planning", "g-b", "t-2", "claude-sonnet-5", "claude", "pat@example.com", Decimal("1.25")),
    ]


def test_cloud_cost_is_read_from_the_billing_export_and_restated_days_replace_what_was_there(clean, monkeypatch):
    asked: list[str] = []
    bill = [("2026-11-10", "Compute Engine", "4.20"), ("2026-11-10", "Cloud Run", "0.30")]

    def bigquery(request: httpx.Request) -> httpx.Response:
        if "metadata" in request.url.host:
            return httpx.Response(200, json={"access_token": "t"})
        asked.append(request.content.decode())
        rows = [{"f": [{"v": d}, {"v": s}, {"v": u}]} for d, s, u in bill]
        return httpx.Response(200, json={"jobComplete": True, "rows": rows})

    with db.connect(autocommit=True) as conn:
        assert cloud_source.harvest(conn, date(2026, 11, 11), httpx.MockTransport(bigquery)) == 0, "no export named"
        monkeypatch.setenv(cloud_source.SETTING, "billing-proj.billing.gcp_billing_export_v1_ABC")
        assert cloud_source.harvest(conn, date(2026, 11, 11), httpx.MockTransport(bigquery)) == 2
        assert "billing-proj.billing.gcp_billing_export_v1_ABC" in asked[0] and "2026-10-01" in asked[0]
        bill[0] = ("2026-11-10", "Compute Engine", "4.35")
        cloud_source.harvest(conn, date(2026, 11, 11), httpx.MockTransport(bigquery))
        assert store.total(conn, date(2026, 11, 1), date(2026, 11, 30), {"source": ["cloud"]}) == Decimal("4.65")

    monkeypatch.setenv(cloud_source.SETTING, "not a table")
    with pytest.raises(RuntimeError, match="MH_BILLING_EXPORT_TABLE must be <project>.<dataset> or"):
        costs_api.check_settings()


def test_named_by_its_dataset_the_export_table_is_found_in_it(clean, monkeypatch):
    tables: list[str] = []
    asked: list[str] = []

    def bigquery(request: httpx.Request) -> httpx.Response:
        if "metadata" in request.url.host:
            return httpx.Response(200, json={"access_token": "t"})
        if request.method == "GET":
            return httpx.Response(200, json={"tables": [{"tableReference": {"tableId": t}} for t in tables]})
        asked.append(request.content.decode())
        return httpx.Response(
            200, json={"jobComplete": True, "rows": [{"f": [{"v": "2026-11-10"}, {"v": "Cloud Run"}, {"v": "0.3"}]}]}
        )

    monkeypatch.setenv(cloud_source.SETTING, "meta-harness-prod.billing_export")
    with db.connect(autocommit=True) as conn:
        with pytest.raises(cloud_source.CloudCostError, match="has no billing export table yet"):
            cloud_source.harvest(conn, date(2026, 11, 11), httpx.MockTransport(bigquery))
        tables[:] = ["gcp_billing_export_resource_v1_ABC", "gcp_billing_export_v1_ABC"]
        assert cloud_source.harvest(conn, date(2026, 11, 11), httpx.MockTransport(bigquery)) == 1
    assert "`meta-harness-prod.billing_export.gcp_billing_export_v1_ABC`" in asked[0]


@pytest.fixture
def api(clean, monkeypatch):
    class FakeTracker:
        async def list_open(self, repo, label):
            return []

    async def _no_title(statement: str):
        from meta_harness.planner.drafting import DraftError

        raise DraftError("no model in tests")

    monkeypatch.delenv(cloud_source.SETTING, raising=False)
    _harvest(_events())
    with db.connect(autocommit=True) as conn:
        store.replace(
            conn, "cloud", date(2026, 11, 1), date(2026, 11, 30),
            [store.Cost(day=date(2026, 11, 10), source="cloud", item="Compute Engine", usd=Decimal("4.20"))],
        )  # fmt: skip
    app = create_app(
        Events(), FakeTracker(), Tasks(TASKS), operator_keys={"op-key": "dave@example.com"},
        service_keys={"svc-key": "edge"}, sweep_seconds=0, titler=_no_title,
    )  # fmt: skip
    with TestClient(app) as client:
        yield client


SPAN = {"from": "2026-11-09", "to": "2026-11-11"}


def test_an_admin_sees_every_source_as_a_series_with_totals_and_a_trend(api):
    seen = api.get("/v1/costs", params=SPAN, headers=OPERATOR).json()
    assert seen["scope"] == "all" and seen["buckets"] == ["2026-11-09", "2026-11-10", "2026-11-11"]
    assert {s["key"]: s["usd"] for s in seen["series"]} == {"cloud": [0.0, 4.2, 0.0], "model": [0.0, 3.25, 1.26]}
    assert seen["totals"] == [0.0, 7.45, 1.26] and seen["total"] == 8.71
    assert seen["daily_average"] == round(8.71 / 3, 4) and seen["trend"]["slope"] == 0.63
    assert seen["top_tasks"][0] == {"task_id": "t-1", "title": "Fix it", "usd": 3.25}
    assert set(seen["sources"]) == {"model", "cloud", "temporal"} and not seen["sources"]["cloud"]["configured"]

    by_model = api.get("/v1/costs", params={**SPAN, "group_by": "model", "source": "model"}, headers=OPERATOR).json()
    assert {s["key"]: s["total"] for s in by_model["series"]} == {
        "claude-sonnet-5": 3.75,
        "claude-opus-5-5": 0.75,
        "claude-haiku-5": 0.01,
    }
    by_goal = api.get("/v1/costs", params={**SPAN, "group_by": "goal", "kind": "review,task"}, headers=OPERATOR).json()
    assert [s["key"] for s in by_goal["series"]] == ["g-a"] and by_goal["total"] == 3.25
    assert api.get("/v1/costs", params={"from": "2026-11-11", "to": "2026-11-09"}, headers=OPERATOR).status_code == 422


def test_a_regular_user_sees_only_the_model_cost_of_their_own_work(api):
    as_pat = {**SERVICE, "X-MH-Actor": "pat@example.com", "X-MH-Role": "user"}
    seen = api.get("/v1/costs", params={**SPAN, "source": "cloud,model"}, headers=as_pat).json()
    assert seen["scope"] == "own" and seen["total"] == 1.25, "their planning task, and no cloud cost"
    assert [s["key"] for s in seen["series"]] == ["model"] and set(seen["sources"]) == {"model"}
    assert [t["task_id"] for t in seen["top_tasks"]] == ["t-2"]
    assert api.post("/v1/costs/harvest", headers=as_pat).status_code == 403

    as_admin = {**SERVICE, "X-MH-Actor": "dave@example.com", "X-MH-Role": "admin"}
    assert api.get("/v1/costs", params=SPAN, headers=as_admin).json()["scope"] == "all"


def test_the_harvest_setting_is_checked_at_startup(monkeypatch):
    monkeypatch.setenv(costs_api.HARVEST_SETTING, "often")
    with pytest.raises(RuntimeError, match="MH_COSTS_HARVEST_SECONDS must be a number of seconds"):
        costs_api.check_settings()
    monkeypatch.setenv(costs_api.HARVEST_SETTING, "0")
    assert costs_api.harvest_seconds() == 0
    assert uuid.uuid4()


def test_a_harvest_reads_from_the_first_of_the_previous_month_where_google_fills_a_new_export_from():
    assert cloud_source.first_day(date(2026, 11, 11)) == date(2026, 10, 1)
    assert cloud_source.first_day(date(2026, 3, 31)) == date(2026, 2, 1)
    assert cloud_source.first_day(date(2026, 1, 5)) == date(2025, 12, 1)


def test_a_filters_choices_are_not_narrowed_by_its_own_value(api):
    picked = api.get(
        "/v1/costs", params={**SPAN, "source": "cloud", "model": "claude-opus-5-5"}, headers=OPERATOR
    ).json()
    assert picked["choices"]["source"] == ["cloud", "model"], "the source filter still offers both, to choose back"
    assert picked["choices"]["kind"] == ["Compute Engine"], "within the cloud source, its billed services"


# Each task's issue labels and pull request outcome, as the planner gives them to the cost API.
FACTS = {
    "t-1": {"labels": ["bug", "saves"], "outcome": "merged"},
    "t-2": {"labels": ["bug"], "outcome": "open"},
}


@pytest.fixture
def by_facts(clean, monkeypatch):
    from fastapi import FastAPI

    monkeypatch.delenv(cloud_source.SETTING, raising=False)
    _harvest(_events())
    with db.connect(autocommit=True) as conn:
        store.replace(
            conn, "cloud", date(2026, 11, 1), date(2026, 11, 30),
            [store.Cost(day=date(2026, 11, 10), source="cloud", item="Compute Engine", usd=Decimal("4.20"))],
        )  # fmt: skip
    app = FastAPI()
    costs_api.register(app, lambda: "dave@example.com", lambda: True, Events(), Tasks(TASKS), GOALS.get, lambda: FACTS)
    with TestClient(app) as client:
        yield client


def test_costs_are_filtered_by_the_labels_of_a_tasks_issue_and_its_pull_requests_outcome(by_facts):
    def seen(**params) -> dict:
        response = by_facts.get("/v1/costs", params={**SPAN, **params})
        assert response.status_code == 200, response.text
        return response.json()

    merged = seen(outcome="merged")
    assert merged["total"] == 3.25 and [t["task_id"] for t in merged["top_tasks"]] == ["t-1"]
    assert seen(outcome="merged,open")["total"] == 4.5
    assert seen(label="bug")["total"] == 4.5 and seen(label="saves")["total"] == 3.25
    assert seen(label="saves", outcome="open")["total"] == 0, "both filters hold"
    assert seen(label="nobody-uses-this")["total"] == 0
    assert seen(source="model")["choices"]["label"] == ["bug", "saves"]
    assert seen(source="model", outcome="open")["choices"]["outcome"] == ["merged", "open"], "not narrowed by itself"
    assert by_facts.get("/v1/costs", params={**SPAN, "outcome": "abandoned"}).status_code == 422


def test_a_breakdown_by_label_shares_a_tasks_cost_among_its_labels_so_the_series_add_up(by_facts):
    by_label = by_facts.get("/v1/costs", params={**SPAN, "group_by": "label", "source": "model"}).json()
    assert {s["key"]: s["total"] for s in by_label["series"]} == {"bug": 2.875, "saves": 1.625, "": 0.01}
    assert round(sum(s["total"] for s in by_label["series"]), 4) == by_label["total"] == 4.51
    by_outcome = by_facts.get("/v1/costs", params={**SPAN, "group_by": "outcome", "source": "model"}).json()
    assert {s["key"]: s["total"] for s in by_outcome["series"]} == {"merged": 3.25, "open": 1.25, "": 0.01}
