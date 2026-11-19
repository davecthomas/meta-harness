"""The planner's updates on a goal: written from its records when its state changes, and only then.

The planner's API runs against Postgres with fakes for the tracker, the Task API and the event service. The
model is a fake writer, or a mock HTTP Gateway: no model is called.
"""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from meta_harness import db
from meta_harness.config import kinds
from meta_harness.planner import planning, updates
from meta_harness.planner.service import create_app

from .test_planner import OPERATOR, Events, FakeTasks, _no_title, migrated, registry  # noqa: F401
from .test_planning import WritingTracker, _feature, _planned, draft


class Writer:
    """Writes what it is told to, and keeps what it was given."""

    def __init__(self):
        self.calls: list[tuple[dict, dict | None]] = []
        self.fail: str | None = None

    async def __call__(self, found: dict, before: dict | None) -> updates.Written:
        if self.fail:
            raise updates.UpdateError(self.fail)
        self.calls.append((found, before))
        return updates.Written(
            headline=f"Update {len(self.calls)}",
            body="The plan is being drafted.",
            needs_you=[{"what": "Approve the plan.", "ref": ""}],
            next="Tasks start once you approve.",
            model="claude:test-model",
            usd_cost="0.0021",
        )


@pytest.fixture
def world():
    events, tracker, tasks, writer = Events(), WritingTracker(), FakeTasks(), Writer()
    app = create_app(
        events,
        tracker,
        tasks,
        operator_keys={"op-key": "dave"},
        service_keys={},
        sweep_seconds=0,
        titler=_no_title,
        update_writer=writer,
    )
    with TestClient(app) as client:
        yield client, events, tasks, writer


@pytest.fixture
def at_once(monkeypatch):
    """No wait between two updates of a goal."""
    shipped = planning.settings()
    monkeypatch.setattr(planning, "settings", lambda: {**shipped, "updates": {"min_seconds": 0}})


def _write(goal: dict, tasks, events, writer, **more) -> dict | None:
    return asyncio.run(updates.write(goal, tasks, events, writer, **more))


def _goal(client, goal_id: str) -> dict:
    return client.get(f"/v1/goals/{goal_id}", headers=OPERATOR).json()


def _found(**issue) -> dict:
    one = {
        "ref": "github:o/r#1",
        "state": "open",
        "task": "running",
        "pull_request": None,
        "held": None,
        "refused": None,
        "waiting_on_operator_for": None,
        "task_reason": "attempt 1",
        **issue,
    }
    return {"goal": {"status": "active", "spent_usd": "1.00"}, "plan": None, "issues": [one]}


def test_the_digest_changes_when_the_goal_moves_and_not_when_it_only_spends():
    before = updates.digest(_found())
    spent = _found(task_reason="attempt 2")
    spent["goal"]["spent_usd"] = "4.00"

    assert updates.digest(spent) == before, "spend and a reason's wording are no news"
    assert updates.digest(_found(task="awaiting_review")) != before
    assert updates.digest(_found(pull_request="https://github.com/o/r/pull/9")) != before
    assert updates.digest(_found(held="large")) != before
    assert updates.digest({**_found(), "goal": {"status": "met"}}) != before


def test_an_update_is_written_once_for_a_state_and_again_when_the_goal_moves(world, at_once):
    client, events, tasks, writer = world
    goal = _feature(client)

    first = _write(goal, tasks, events, writer)

    assert (first["number"], first["headline"], first["needs_you"]) == (
        1,
        "Update 1",
        [{"what": "Approve the plan.", "ref": ""}],
    )
    found, before = writer.calls[0]
    assert before is None and found["goal"]["status"] == "planning" and found["plan"]["planning_task"] == "queued"
    assert (
        "goal_update_written",
        {"goal_id": goal["id"], "number": 1, "model": "claude:test-model", "usd_cost": "0.0021"},
    ) in events.events
    assert _write(goal, tasks, events, writer) is None, "nothing moved: nothing is written, and nothing spent"
    assert len(writer.calls) == 1

    _planned(client, events, tasks, goal, [draft("store"), draft("api", ("store",))])
    second = _write(_goal(client, goal["id"]), tasks, events, writer)

    assert second["number"] == 2
    found, before = writer.calls[1]
    assert before["headline"] == "Update 1", "the writer is given its last update, to say what changed"
    assert (found["plan"]["version"], found["plan"]["new_tasks"], found["plan"]["approved"]) == (1, 2, False)
    shown = client.get(f"/v1/goals/{goal['id']}/updates", headers=OPERATOR).json()
    assert [u["number"] for u in shown["updates"]] == [2, 1] and shown["enabled"] is True and shown["error"] is None


def test_two_updates_keep_their_distance_and_a_goal_has_only_its_share(world, monkeypatch):
    client, events, tasks, writer = world
    goal = _feature(client)
    assert _write(goal, tasks, events, writer)["number"] == 1
    _planned(client, events, tasks, goal, [draft("store")])
    moved = _goal(client, goal["id"])

    def asks(needs: str) -> None:
        with db.connect(autocommit=True) as conn:
            conn.execute("UPDATE planner.goal_update SET needs_you = %s::jsonb WHERE goal_id = %s", (needs, goal["id"]))

    asks("[]")
    assert _write(moved, tasks, events, writer) is None, "the last update is seconds old: the next pass writes it"

    # One that asked something of the operator does not wait: the goal moved, so what it asked may be done.
    asks('[{"what": "Approve the plan.", "ref": ""}]')
    assert _write(moved, tasks, events, writer)["number"] == 2

    shipped = planning.settings()
    monkeypatch.setattr(planning, "settings", lambda: {**shipped, "updates": {"max_per_goal": 3}})
    asked = client.post(f"/v1/goals/{goal['id']}/updates", headers=OPERATOR)
    assert asked.status_code == 200 and asked.json()["update"]["number"] == 3, "one asked for is written at once"
    refused = client.post(f"/v1/goals/{goal['id']}/updates", headers=OPERATOR)
    assert refused.status_code == 502 and "updates.max_per_goal" in refused.json()["detail"]
    assert len(writer.calls) == 3


def test_an_update_that_cannot_be_written_says_so_with_what_to_do_and_keeps_the_last(world):
    client, events, tasks, writer = world
    goal = _feature(client)
    _write(goal, tasks, events, writer)
    writer.fail = "the HTTP Gateway answered 503: busy"

    refused = client.post(f"/v1/goals/{goal['id']}/updates", headers=OPERATOR)

    assert refused.status_code == 502
    assert (
        refused.json()["detail"]
        == "the update could not be written: the HTTP Gateway answered 503: busy. Ask again in a moment"
    )
    assert [u["number"] for u in client.get(f"/v1/goals/{goal['id']}/updates", headers=OPERATOR).json()["updates"]] == [
        1
    ]


def test_updates_turned_off_write_nothing_and_asking_says_where_to_turn_them_on(world, monkeypatch):
    client, events, tasks, writer = world
    goal = _feature(client)
    shipped = planning.settings()
    monkeypatch.setattr(planning, "settings", lambda: {**shipped, "updates": {"enabled": False}})

    assert _write(goal, tasks, events, writer) is None and writer.calls == []
    refused = client.post(f"/v1/goals/{goal['id']}/updates", headers=OPERATOR)
    assert refused.status_code == 409 and "Configuration, Planning" in refused.json()["detail"]
    assert client.get(f"/v1/goals/{goal['id']}/updates", headers=OPERATOR).json()["enabled"] is False


def test_a_pass_covers_open_goals_and_a_closed_one_only_for_its_last_word(world, at_once):
    client, events, tasks, writer = world
    quiet, told = _feature(client), _feature(client)
    _write(told, tasks, events, writer)
    for goal in (quiet, told):
        assert client.post(f"/v1/goals/{goal['id']}/cancel", headers=OPERATOR).status_code == 200

    listed = updates.goals()
    covered = {goal["id"] for goal in listed}
    # A goal as the pass reads it, straight from the database, makes facts a model call can carry.
    json.dumps([updates.facts(goal, [], [], None) for goal in listed])

    assert told["id"] in covered, "a goal the planner spoke of gets a last update when it closes"
    assert quiet["id"] not in covered
    last = _write(_goal(client, told["id"]), tasks, events, writer)
    assert last["number"] == 2 and writer.calls[-1][0]["goal"]["status"] == "canceled"


async def test_the_model_call_is_one_structured_request_on_the_configured_model(monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", "http://gateway.test")
    monkeypatch.setenv("GATEWAY_API_KEY", "k")
    seen = []
    answer = {
        "headline": "Two of three tasks are done.",
        "body": "The API task merged.",
        "needs_you": [],
        "next": "The UI task starts.",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"data": answer, "finish_reason": "complete", "usd_cost": "0.0031"})

    written = await updates.write_update(
        _found(), {"headline": "h", "body": "b", "needs_you": [], "next": ""}, httpx.MockTransport(handler)
    )

    assert (written.headline, written.next, written.usd_cost) == (
        "Two of three tasks are done.",
        "The UI task starts.",
        "0.0031",
    )
    config = updates.settings()
    assert f"{seen[0]['engine']}:{seen[0]['model']}" == config["model"] == written.model
    assert seen[0]["max_response_tokens"] == config["max_response_tokens"]
    assert "--- your last update ---" in seen[0]["prompt"] and '"task": "running"' in seen[0]["prompt"]

    with pytest.raises(updates.UpdateError, match="answered 400"):
        await updates.write_update(_found(), None, httpx.MockTransport(lambda r: httpx.Response(400, text="no")))


def test_the_update_settings_are_checked_and_fall_back_to_goal_draftings_model(monkeypatch):
    shipped = planning.settings()
    assert kinds.check_planning(shipped, None) == []

    def reasons(**changed) -> list[str]:
        return kinds.check_planning({**shipped, "updates": {**shipped["updates"], **changed}}, None)

    assert reasons(enabled="no")[0].startswith("updates.enabled must be true or false")
    assert reasons(model="haiku")[0].startswith("updates.model must be <engine>:<model>")
    assert reasons(max_per_goal=0)[0].startswith("updates.max_per_goal must be a whole number above 0")
    assert reasons(min_seconds=1.5)[0].startswith("updates.min_seconds must be a whole number above 0")
    assert kinds.check_planning({k: v for k, v in shipped.items() if k != "updates"}, None) == []

    monkeypatch.setattr(planning, "settings", lambda: {k: v for k, v in shipped.items() if k != "updates"})
    from meta_harness.planner import drafting

    drafter = drafting.settings()
    assert (
        updates.settings()["model"] == f"{drafter['engine']}:{drafter['model']}"
        and updates.settings()["enabled"] is True
    )


def test_the_facts_say_where_each_task_stands_and_whether_it_waits_on_the_operator():
    link = {"state": "open", "held_reason": None, "refused_reason": None}
    reviewing = updates.stage(link, {"status": "running", "pr_url": "https://github.com/o/r/pull/9"}, False)

    assert "its own review" in reviewing and reviewing.endswith("Nothing is needed from the operator yet")
    assert updates.stage(link, {"status": "running"}, False).startswith("an agent is writing the change")
    assert updates.stage(link, {"status": "ready"}, False).endswith("It waits on the operator to merge it")
    assert updates.stage(link, {"status": "ready"}, True).endswith("Nothing is needed from the operator")
    assert (
        updates.stage(link, {"status": "running", "waiting_on": "an answer"}, False)
        == "waits on the operator: an answer"
    )
    assert updates.stage({**link, "held_reason": "large"}, None, False).endswith("It waits on the operator")
    assert updates.stage(link, None, False).startswith("not started yet")
    assert updates.stage({**link, "state": "closed"}, {"status": "closed"}, False) == "done: its issue is closed"
    assert "never for something a fact says needs nothing from them" in updates.SYSTEM_PROMPT
