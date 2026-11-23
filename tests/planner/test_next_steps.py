"""A goal's next steps: written from its records, kept until they change, and each one click from a new goal."""

import json
import time
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from meta_harness import db
from meta_harness.planner import next_steps, planning

from .test_planner import OPERATOR, Events, FakeTasks, _no_title, migrated, registry  # noqa: F401
from .test_planning import WritingTracker, _feature

STEPS = [
    {
        "title": "Make the header logo readable",
        "why": "The plan listed the logo's size as a gap.",
        "statement": "Size the header's logo so its name reads. Done when the letters are 16 pixels or taller.",
    },
    {
        "title": "Rename the text the services write",
        "why": "The specification left pull request text out of scope.",
        "statement": "Rename the product in the text written to pull requests. Done when a test finds no old name.",
    },
]


class Writer:
    """Stands in for the model: answers with `steps`, and keeps each prompt it was given."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.steps = STEPS
        self.fails = False

    async def __call__(self, system: str, prompt: str):
        if self.fails:
            raise next_steps.NextStepsError("the HTTP Gateway is unreachable")
        self.calls.append((system, prompt))
        return [dict(step) for step in self.steps], "engine:cheap", "0.003"


@pytest.fixture
def world(monkeypatch):
    from meta_harness.planner.service import create_app

    real = planning.settings
    monkeypatch.setattr(planning, "settings", lambda: {**real(), "narratives": {"max_usd": "1.00"}})
    with db.connect(autocommit=True) as conn:
        conn.execute("TRUNCATE planner.narrative_spend")
    events, tasks, writer = Events(), FakeTasks(), Writer()
    app = create_app(
        events,
        WritingTracker(),
        tasks,
        operator_keys={"op-key": "dave"},
        service_keys={},
        sweep_seconds=0,
        titler=_no_title,
        next_steps_writer=writer,
    )
    with TestClient(app) as client:
        yield client, events, tasks, writer


def _read(client, goal_id: str, query: str = "") -> dict:
    """The read once nothing is being written: steps are written in the background, and a page reads again."""
    for _ in range(200):
        response = client.get(f"/v1/goals/{goal_id}/next-steps{query}", headers=OPERATOR)
        if response.status_code != 200 or not response.json()["writing"]:
            return response
        time.sleep(0.02)
    raise AssertionError("the next steps were still being written")


def test_next_steps_are_written_once_from_the_goals_facts_and_read_from_storage_after(world):
    client, events, tasks, writer = world
    goal = _feature(client, max_usd="40")

    # Asked without writing, as a running goal's page does: nothing is written, and nothing spent.
    quiet = client.get(f"/v1/goals/{goal['id']}/next-steps?write=false", headers=OPERATOR).json()
    assert (quiet["steps"], quiet["writing"], writer.calls) == ([], False, [])

    first = client.get(f"/v1/goals/{goal['id']}/next-steps", headers=OPERATOR).json()
    assert first["writing"] is True and first["steps"] == [], "the read does not wait for the model"
    shown = _read(client, goal["id"]).json()

    assert [(s["title"], s["goal"]) for s in shown["steps"]] == [(s["title"], None) for s in STEPS]
    assert all(s["id"] == next_steps.step_id(s["statement"]) and s["why"] for s in shown["steps"])
    assert (shown["budget_usd"], shown["started"], shown["continues"]) == ("40.00", [], None)
    system, prompt = writer.calls[0]
    assert "what you would do next if you were carrying" in system and "Add exports" in prompt
    assert '"started": []' in prompt
    written = [e[1] for e in events.events if e[0] == "next_steps_written"]
    assert written == [
        {
            "goal_id": goal["id"],
            "digest": written[0]["digest"],
            "steps": 2,
            "model": "engine:cheap",
            "usd_cost": "0.003",
        }
    ]
    # Read again with nothing changed: from storage.
    assert _read(client, goal["id"]).json()["steps"] == shown["steps"] and len(writer.calls) == 1


def test_one_click_starts_a_planned_goal_with_the_first_goals_settings_and_twice_makes_one(world):
    client, events, tasks, writer = world
    goal = _feature(client, max_usd="40", auto_merge=True, strict_issues=True)
    [step, other] = _read(client, goal["id"]).json()["steps"]

    made = client.post(f"/v1/goals/{goal['id']}/next-steps/{step['id']}/goal", headers=OPERATOR)

    assert made.status_code == 201 and made.json()["already"] is False
    new = client.get(f"/v1/goals/{made.json()['goal_id']}", headers=OPERATOR).json()
    assert (new["statement"], new["title"]) == (STEPS[0]["statement"], STEPS[0]["title"])
    assert (new["status"], new["modality"], new["repos"]) == ("planning", "feature", goal["repos"])
    assert (Decimal(new["max_usd"]), new["auto_merge"], new["strict_issues"]) == (Decimal(40), True, True)
    assert new["plan_task_id"], "it is planned first: nothing runs before its plan is approved"
    assert new["label"] != goal["label"], "its tasks carry a label of its own"

    again = client.post(f"/v1/goals/{goal['id']}/next-steps/{step['id']}/goal", headers=OPERATOR)
    assert again.json() == {"goal_id": new["id"], "already": True}

    # The step shows its goal, the new goal says what it continues, and the next writing is told of it.
    shown = _read(client, goal["id"]).json()
    assert shown["started"] == [{"id": new["id"], "title": STEPS[0]["title"], "status": "planning"}]
    assert STEPS[0]["title"] in writer.calls[-1][1] and len(writer.calls) == 2
    assert [s["goal"] and s["goal"]["id"] for s in shown["steps"]] == [new["id"], None]
    continued = client.get(f"/v1/goals/{new['id']}/next-steps?write=false", headers=OPERATOR).json()
    assert continued["continues"] == {"id": goal["id"], "title": goal["title"] or goal["statement"]}

    gone = client.post(f"/v1/goals/{goal['id']}/next-steps/nosuchstep00/goal", headers=OPERATOR)
    assert gone.status_code == 409 and "Reload the page" in gone.json()["detail"]
    assert other["goal"] is None


def test_a_goal_with_no_budget_hands_on_a_limit_never_none(world):
    client, events, tasks, writer = world
    goal = _feature(client)
    [step, _] = _read(client, goal["id"]).json()["steps"]
    assert _read(client, goal["id"]).json()["budget_usd"] == "100.00"

    made = client.post(f"/v1/goals/{goal['id']}/next-steps/{step['id']}/goal", headers=OPERATOR).json()

    new = client.get(f"/v1/goals/{made['goal_id']}", headers=OPERATOR).json()
    assert Decimal(new["max_usd"]) == next_steps.DEFAULT_BUDGET_USD


def test_steps_that_cannot_be_written_say_why_and_what_to_do(world, monkeypatch):
    client, events, tasks, writer = world
    goal = _feature(client)
    writer.fails = True

    client.get(f"/v1/goals/{goal['id']}/next-steps", headers=OPERATOR)
    refused = _read(client, goal["id"])

    assert refused.status_code == 503
    assert (
        "the HTTP Gateway is unreachable" in refused.json()["detail"]
        and "ask again in a minute" in refused.json()["detail"]
    )

    # Past the day's limit, which narratives and next steps share, the reason names the setting.
    writer.fails = False
    with db.connect(autocommit=True) as conn:
        conn.execute("INSERT INTO planner.narrative_spend (day, usd) VALUES ((now() AT TIME ZONE 'utc')::date, 5)")
    over = client.get(f"/v1/goals/{goal['id']}/next-steps", headers=OPERATOR)
    assert over.status_code == 503 and "raise narratives.max_usd" in over.json()["detail"]
    assert writer.calls == []


async def test_the_model_is_asked_with_a_schema_its_provider_takes_and_no_more_steps_are_kept(monkeypatch):
    """The first deployed call was refused: "For 'array' type, property 'maxItems' is not supported"."""
    monkeypatch.setenv("GATEWAY_URL", "http://gateway")
    monkeypatch.setenv("GATEWAY_API_KEY", "gw-key")

    def keys(node) -> set[str]:
        if isinstance(node, dict):
            return set(node) | {k for v in node.values() for k in keys(v)}
        return {k for v in node for k in keys(v)} if isinstance(node, list) else set()

    assert not keys(next_steps.SCHEMA) & {"maxItems", "minItems", "minLength", "maxLength", "pattern"}
    sent = []
    five = [{"title": f"Step {n}", "why": "A gap the plan listed.", "statement": f"Do thing {n}."} for n in range(5)]

    def gateway(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"data": {"steps": five}, "finish_reason": "complete", "usd_cost": "0.002"})

    async def as_written(text, where):
        return text

    monkeypatch.setattr(next_steps.outbound, "prepare", as_written)

    steps, model, cost = await next_steps.write(next_steps.PROMPT, "--- facts ---", httpx.MockTransport(gateway))

    assert [s["title"] for s in steps] == ["Step 0", "Step 1", "Step 2"] and cost == "0.002"
    assert (
        sent[0]["response_schema"] == next_steps.SCHEMA
        and f"at most {next_steps.MAX_STEPS}" in sent[0]["system_prompt"]
    )
