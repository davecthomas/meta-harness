"""Narrative summaries: one stored for each goal and reader, written again only when the goal changes."""

import pytest
from fastapi.testclient import TestClient

from meta_harness import db
from meta_harness.planner import narratives, planning

from .test_planner import OPERATOR, Events, FakeTasks, FakeTracker, _goal, _label, _no_title, migrated, registry  # noqa: F401

PERSONAS = {
    "product": {"label": "Product", "reader": "A product manager. They care about what users get."},
    "developer": {"label": "Developer", "reader": "A developer. They care about what changed in the code."},
}


class Writer:
    """Stands in for the model: each call it was asked for, answered with a line naming the reader."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.fails = False

    async def __call__(self, system: str, prompt: str):
        if self.fails:
            raise narratives.NarrativeError("the HTTP Gateway is unreachable")
        self.calls.append((system, prompt))
        who = "product" if "product manager" in system else "developer"
        return f"A narrative for {who}, number {len(self.calls)}.", "engine:cheap", "0.002"


@pytest.fixture
def world(monkeypatch):
    from meta_harness.planner.service import create_app

    real = planning.settings
    monkeypatch.setattr(
        planning, "settings", lambda: {**real(), "narratives": {"max_usd": "1.00", "personas": PERSONAS}}
    )
    with db.connect(autocommit=True) as conn:
        conn.execute("TRUNCATE planner.narrative, planner.narrative_spend")
    events, tasks, writer = Events(), FakeTasks(), Writer()
    app = create_app(
        events,
        FakeTracker(),
        tasks,
        operator_keys={"op-key": "dave"},
        service_keys={},
        sweep_seconds=0,
        titler=_no_title,
        narrative_writer=writer,
    )
    with TestClient(app) as client:
        yield client, events, tasks, writer


def _read(client, goal_id: str, persona: str):
    return client.get(f"/v1/goals/{goal_id}/narrative", params={"persona": persona}, headers=OPERATOR)


def test_a_goals_narrative_is_written_once_for_each_reader_and_read_from_storage_after(world):
    client, events, tasks, writer = world
    goal = _goal(client, _label())

    assert [p["id"] for p in client.get("/v1/overview/personas", headers=OPERATOR).json()["personas"]] == [
        "product",
        "developer",
    ]
    first = _read(client, goal["id"], "product").json()
    assert (first["text"], first["label"], first["stale"]) == ("A narrative for product, number 1.", "Product", False)
    system, prompt = writer.calls[0]
    assert "The reader: A product manager." in system and "Write only what the facts say" in system
    assert "--- facts ---" in prompt and '"statement": "Fix bugs"' in prompt

    # Opening the page again spends nothing: the stored narrative is served while the goal stands still.
    again = _read(client, goal["id"], "product").json()
    assert again["text"] == first["text"] and again["written_at"] == first["written_at"] and len(writer.calls) == 1

    # Another reader gets their own, written from the same facts and shaped toward them.
    other = _read(client, goal["id"], "developer").json()
    assert other["text"] == "A narrative for developer, number 2." and len(writer.calls) == 2
    assert [e[1]["persona"] for e in events.events if e[0] == "narrative_written"] == ["product", "developer"]


def test_a_narrative_is_written_again_only_when_the_goal_has_changed(world):
    client, _, tasks, writer = world
    goal = _goal(client, _label())
    _read(client, goal["id"], "product")

    with db.connect(autocommit=True) as conn:
        conn.execute("UPDATE planner.goal SET status = 'paused' WHERE id = %s", (goal["id"],))
    moved = _read(client, goal["id"], "product").json()

    assert moved["text"] == "A narrative for product, number 2." and len(writer.calls) == 2
    assert _read(client, goal["id"], "product").json()["text"] == moved["text"] and len(writer.calls) == 2


def test_a_narrative_that_cannot_be_written_now_is_served_as_stored_and_says_so(world):
    client, _, _, writer = world
    goal = _goal(client, _label())
    writer.fails = True

    none = _read(client, goal["id"], "product")
    assert none.status_code == 503 and "there is no narrative to show yet" in none.json()["detail"]
    assert "open this again in a minute" in none.json()["detail"], "the refusal says what to do"

    writer.fails = False
    kept = _read(client, goal["id"], "product").json()
    with db.connect(autocommit=True) as conn:
        conn.execute("UPDATE planner.goal SET status = 'paused' WHERE id = %s", (goal["id"],))
    writer.fails = True
    older = _read(client, goal["id"], "product").json()
    assert older["text"] == kept["text"] and older["stale"] is True and "could not be written just now" in older["why"]


def test_narratives_stop_at_their_cost_for_the_day_and_an_unknown_reader_is_refused(world, monkeypatch):
    client, _, _, writer = world
    goal = _goal(client, _label())
    with db.connect(autocommit=True) as conn:
        conn.execute("INSERT INTO planner.narrative_spend (day, usd) VALUES ((now() AT TIME ZONE 'utc')::date, 1.00)")

    capped = _read(client, goal["id"], "product")

    assert capped.status_code == 503 and writer.calls == []
    assert (
        "have cost their $1.00 for today" in capped.json()["detail"] and "narratives.max_usd" in capped.json()["detail"]
    )
    unknown = _read(client, goal["id"], "investor")
    assert unknown.status_code == 422 and "choose one of product, developer" in unknown.json()["detail"]


def test_the_overviews_summary_is_written_from_the_goals_narratives_and_kept_until_one_changes(world):
    client, _, _, writer = world
    with db.connect(autocommit=True) as conn:
        conn.execute(
            "UPDATE planner.goal SET status = 'canceled' WHERE status IN ('active', 'planning', 'paused', 'stalled', 'budget_exhausted')"
        )
    first, second = _goal(client, _label()), _goal(client, _label())

    summary = client.get("/v1/overview/narrative", params={"persona": "product"}, headers=OPERATOR).json()

    assert summary["goals"] == 2 and len(writer.calls) == 3, "each goal's narrative, then the one across them"
    system, prompt = writer.calls[-1]
    assert "one summary across several goals" in system and "The reader: A product manager." in system
    assert "A narrative for product, number 1." in prompt and "A narrative for product, number 2." in prompt
    again = client.get("/v1/overview/narrative", params={"persona": "product"}, headers=OPERATOR).json()
    assert again["text"] == summary["text"] and len(writer.calls) == 3, "nothing changed, so nothing is written"

    # A goal that leaves the running set changes what the summary covers, so it is written again, once.
    with db.connect(autocommit=True) as conn:
        conn.execute("UPDATE planner.goal SET status = 'paused' WHERE id = %s", (second["id"],))
    fewer = client.get("/v1/overview/narrative", params={"persona": "product"}, headers=OPERATOR).json()
    assert fewer["goals"] == 1 and len(writer.calls) == 4
    assert first["id"] != second["id"]


def test_the_planning_configurations_narratives_are_checked():
    assert narratives.check({"narratives": {"personas": PERSONAS}}) == []
    bad = narratives.check({"narratives": {"model": "nocolon", "max_usd": "0", "personas": {"x": {"label": "X"}}}})
    assert len(bad) == 3 and "narratives.personas.x must have a label" in bad[2]


def test_a_configuration_that_names_no_reader_still_has_the_two_that_ship(monkeypatch):
    real = planning.settings
    # As a deployment whose configuration was stored before narratives existed.
    monkeypatch.setattr(planning, "settings", lambda: {k: v for k, v in real().items() if k != "narratives"})

    assert narratives.personas() == [{"id": "product", "label": "Product"}, {"id": "developer", "label": "Developer"}]
    monkeypatch.setattr(
        planning, "settings", lambda: {**real(), "narratives": {"personas": {"exec": PERSONAS["product"]}}}
    )
    assert [p["id"] for p in narratives.personas()] == ["exec"], "one that names its own gets only those"
