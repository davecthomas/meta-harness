# Created by the meta-harness in run t-08ea31be07bf:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""POST /v1/goals/enrich and its accept path, over a fake HTTP Gateway and a store that keeps one goal."""

import contextlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from meta_harness import retrying
from meta_harness.costs import store as costs_store
from meta_harness.planner import enrich, store
from meta_harness.planner import service as planner_service
from meta_harness.planner.service import create_app
from meta_harness.run_service import writing

OPERATOR = {"Authorization": "Bearer op-key"}
REPO = "davecthomas/meta-harness"
STATEMENT = "Make the goal page faster."
ANSWER = {
    "statement": "Make the goal page load in under a second. Out of scope: the board. Covers github:o/r#1.",
    "added": [{"what": "A testable outcome", "why": "The statement named no check."}],
    "questions": ["Is one second the right target?"],
}


class Events:
    def __init__(self):
        self.events = []

    def emit(self, event_type, payload=None, actor=None, **fields):
        self.events.append((event_type, payload or {}, actor))


class Pool:
    @contextlib.contextmanager
    def connection(self):
        yield None

    def close(self):
        pass


@contextlib.contextmanager
def no_database(*args, **kwargs):
    yield None


class Gateway:
    """A fake HTTP Gateway: answers each /v1/structured call from its list, and keeps what it was asked."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.asked: list[dict] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/structured"
        self.asked.append(json.loads(request.content))
        status, body = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return httpx.Response(status, json=body)


def ok(data=None, usd="0.004"):
    return 200, {"data": data or ANSWER, "finish_reason": "complete", "usd_cost": usd}


@pytest.fixture
def world(monkeypatch):
    """The planner over one goal, a fake gateway, a writing pass that drops "really ", and a cost store that
    keeps what it is given."""
    goal = {
        "id": "g-1",
        "title": "Faster goal page",
        "statement": STATEMENT,
        "status": "active",
        "label": "speed",
        "repos": [REPO],
        "tracker": "jira",
        "tracker_settings": {"project": "VL"},
        "created_by": "owner",
    }
    saved: list[dict] = []
    costs: list = []

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

    async def check_all(texts, transport=None):
        outcomes = []
        for text in texts:
            cleaned = text.replace("really ", "")
            kept = cleaned != text
            outcomes.append(
                writing.Outcome(
                    text=cleaned,
                    findings=int(kept),
                    rewritten=kept,
                    kept=kept,
                    before=text if kept else "",
                    model="claude:rewriter" if kept else "",
                    usd_cost="0.001" if kept else "0",
                )
            )
        return outcomes

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(planner_service.db, "pool", lambda url=None: Pool())
    monkeypatch.setattr(enrich.db, "connect", no_database)
    monkeypatch.setattr(store, "get", get)
    monkeypatch.setattr(store, "update", update)
    monkeypatch.setattr(costs_store, "record", lambda conn, found: costs.extend(found))
    monkeypatch.setattr(writing, "check_all", check_all)
    monkeypatch.setattr(retrying, "_async_sleep", no_wait)
    monkeypatch.setenv("GATEWAY_URL", "http://gateway.test")
    monkeypatch.setenv("GATEWAY_API_KEY", "gateway-key")
    gateway = Gateway(ok())
    real = enrich.propose
    monkeypatch.setattr(enrich, "propose", lambda known: real(known, transport=httpx.MockTransport(gateway.handle)))
    events = Events()
    app = create_app(events, None, None, operator_keys={"op-key": "dave"}, service_keys={}, sweep_seconds=0)
    with TestClient(app) as client:
        yield client, goal, gateway, events, saved, costs


def test_a_proposal_answers_a_richer_statement_and_saves_nothing(world):
    client, goal, gateway, events, saved, costs = world
    gateway.responses = [ok({**ANSWER, "statement": "Make it really fast. " + ANSWER["statement"]})]

    answered = client.post(
        "/v1/goals/enrich",
        json={"statement": STATEMENT, "goal_id": "g-1", "issues": [{"ref": "github:o/r#1", "title": "Slow page"}]},
        headers=OPERATOR,
    )

    assert answered.status_code == 200, answered.text
    proposal = answered.json()
    # The writing pass ran on what the model wrote.
    assert proposal["statement"] == "Make it fast. " + ANSWER["statement"]
    assert proposal["added"] == ANSWER["added"] and proposal["questions"] == ANSWER["questions"]
    assert proposal["model"] == "claude:claude-haiku-4-5"
    # The goal is as it was, and nothing was recorded on it.
    assert saved == [] and goal["statement"] == STATEMENT and events.events == []
    # What the model read: the statement, the issues sent, and what the goal says of the rest.
    asked = gateway.asked[0]
    assert (asked["engine"], asked["model"]) == ("claude", "claude-haiku-4-5")
    assert STATEMENT in asked["prompt"] and "github:o/r#1" in asked["prompt"]
    assert '"project": "VL"' in asked["prompt"] and '"label": "speed"' in asked["prompt"]
    for part in ("out of scope", "how success is checked", "constraints", "questions"):
        assert part in asked["system_prompt"]
    # The cost is a planning cost of the goal, and the rewrite a writing cost of it.
    assert [(c.item, str(c.usd), c.goal_id, c.operator) for c in costs] == [
        ("planning", "0.004", "g-1", "owner"),
        ("writing", "0.001", "g-1", "owner"),
    ]


def test_a_proposal_for_no_goal_is_a_planning_cost_of_no_goal(world):
    client, _, _, _, saved, costs = world

    answered = client.post("/v1/goals/enrich", json={"statement": STATEMENT, "repos": [REPO]}, headers=OPERATOR)

    assert answered.status_code == 200, answered.text
    assert saved == []
    assert [(c.item, c.goal_id, c.repo, c.operator) for c in costs] == [("planning", "", REPO, "dave")]


def test_a_proposal_for_an_unknown_goal_is_404_and_asks_no_model(world):
    client, _, gateway, _, _, costs = world

    answered = client.post("/v1/goals/enrich", json={"statement": STATEMENT, "goal_id": "g-x"}, headers=OPERATOR)

    assert answered.status_code == 404 and gateway.asked == [] and costs == []


def test_a_model_that_fails_after_its_retries_answers_502_and_leaves_the_goal_as_it_was(world):
    client, goal, gateway, events, saved, costs = world
    gateway.responses = [(503, {"detail": "busy"})]

    answered = client.post("/v1/goals/enrich", json={"statement": STATEMENT, "goal_id": "g-1"}, headers=OPERATOR)

    assert answered.status_code == 502
    assert "could not propose a statement" in answered.json()["detail"] and "Try again" in answered.json()["detail"]
    # Retried before it was reported.
    assert len(gateway.asked) == retrying.DEFAULT.attempts
    assert saved == [] and goal["statement"] == STATEMENT and events.events == [] and costs == []


def test_a_model_that_does_not_finish_answers_502(world):
    client, _, gateway, _, saved, _ = world
    gateway.responses = [(200, {"data": None, "finish_reason": "length"})]

    answered = client.post("/v1/goals/enrich", json={"statement": STATEMENT}, headers=OPERATOR)

    assert answered.status_code == 502 and "did not finish (length)" in answered.json()["detail"]
    assert saved == []


def test_accept_saves_the_edited_statement_and_records_goal_changed_with_the_user(world):
    client, goal, _, events, saved, _ = world
    edited = "Make the goal page load in under two seconds."

    accepted = client.post("/v1/goals/g-1/enrich/accept", json={"statement": edited}, headers=OPERATOR)

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["statement"] == edited == goal["statement"]
    assert saved == [{"statement": edited}]
    [(kind, payload, actor)] = events.events
    assert kind == "goal_changed" and actor == "dave"
    assert payload["changes"] == {"statement": {"from": STATEMENT, "to": edited}} and payload["enriched"] is True


def test_accept_of_the_same_statement_records_nothing(world):
    client, _, _, events, _, _ = world

    accepted = client.post("/v1/goals/g-1/enrich/accept", json={"statement": STATEMENT}, headers=OPERATOR)

    assert accepted.status_code == 200 and events.events == []


@pytest.mark.parametrize("status", ["met", "canceled"])
def test_accept_refuses_a_goal_that_has_ended(world, status):
    client, goal, _, events, saved, _ = world
    goal["status"] = status

    accepted = client.post("/v1/goals/g-1/enrich/accept", json={"statement": "New."}, headers=OPERATOR)

    assert accepted.status_code == 409 and "reopen" in accepted.json()["detail"]
    assert saved == [] and events.events == [] and goal["statement"] == STATEMENT


def test_goal_enrich_settings_are_checked(monkeypatch):
    enrich.check_settings()
    shipped = enrich.settings()
    for bad, named in (({**shipped, "max_issues": 0}, "max_issues"), ({**shipped, "model": ""}, "model")):
        monkeypatch.setattr(enrich, "settings", lambda bad=bad: bad)
        with pytest.raises(RuntimeError, match=f"goal_enrich.{named}"):
            enrich.check_settings()


def test_a_long_statement_is_cut_at_a_sentence():
    text = "One sentence. " * 400
    cut = enrich.fitted(text)
    assert len(cut) <= enrich.STATEMENT_CHARS and cut.endswith(".")
