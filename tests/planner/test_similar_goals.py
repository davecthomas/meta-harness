"""New goal shows the open goals a description is close to. No test calls the HTTP Gateway."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from meta_harness import db
from meta_harness.planner import similar_goals
from meta_harness.planner.service import create_app

from .test_planner import OPERATOR, Events, FakeTasks, _no_title, migrated, registry  # noqa: F401
from .test_planning import WritingTracker

WORDS = ("compaction", "context", "billing", "invoice", "login")


class Gateway(httpx.AsyncBaseTransport):
    """An embeddings endpoint: each text's vector counts a few words, so texts on one subject are close."""

    def __init__(self) -> None:
        self.asked: list[list[str]] = []
        self.down = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(400, json={"detail": "no such model"})
        body = json.loads(request.content)
        assert request.url.path == "/v1/embeddings" and body["engine"] == "openai"
        self.asked.append(body["inputs"])
        vectors = [
            {"index": i, "embedding": [float(text.lower().count(word)) + 0.01 for word in WORDS]}
            for i, text in enumerate(body["inputs"])
        ]
        return httpx.Response(200, json={"vectors": vectors, "engine": "openai", "model": body["model"]})


@pytest.fixture
def world(migrated, registry, monkeypatch):  # noqa: F811
    monkeypatch.setenv("GATEWAY_URL", "http://gateway.test")
    monkeypatch.setenv("GATEWAY_API_KEY", "k")
    with db.connect(autocommit=True) as conn:
        conn.execute("DELETE FROM planner.goal_embedding")
        conn.execute("UPDATE planner.goal SET status = 'canceled' WHERE status <> 'canceled'")
    events, gateway = Events(), Gateway()
    app = create_app(
        events, WritingTracker(), FakeTasks(), operator_keys={"op-key": "dave"}, service_keys={},
        sweep_seconds=0, titler=_no_title, gateway_transport=gateway,
    )  # fmt: skip
    with TestClient(app) as client:
        yield client, events, gateway


def _goal(client, statement: str) -> dict:
    made = client.post(
        "/v1/goals",
        json={"statement": statement, "repos": ["davecthomas/meta-harness"], "label": "x"},
        headers=OPERATOR,
    )
    assert made.status_code == 201, made.text
    return made.json()


COMPACTION = "Keep the task through context compaction, so a long run does not forget its task after compaction."


@pytest.mark.db
def test_an_open_goal_like_the_description_is_shown_closest_first_and_recorded(world):
    client, events, gateway = world
    same = _goal(client, "Runs keep their task when context compaction happens, and compaction is cheaper.")
    _goal(client, "Billing sends each invoice once, and the invoice shows the billing period.")
    over = _goal(client, "Old work on context compaction and compaction summaries, since abandoned.")
    client.post(f"/v1/goals/{over['id']}/cancel", headers=OPERATOR)

    found = client.post("/v1/goals/similar", json={"description": COMPACTION}, headers=OPERATOR).json()

    assert found["checked"] is True and found["cutoff"] == 0.7 and found["model"] == "openai:text-embedding-3-small"
    assert [g["goal_id"] for g in found["goals"]] == [same["id"]], (
        "the billing goal is not close, and a canceled one is not compared"
    )
    assert found["goals"][0]["score"] > 0.9 and found["goals"][0]["status"] in ("planning", "active")
    shown = [e[1] for e in events.events if e[0] == "goal_similarity_judged"]
    assert [(e["goal_id"], e["answer"]) for e in shown] == [(same["id"], "shown")]
    assert "description" not in shown[0] and COMPACTION not in json.dumps(shown), (
        "the new description is never recorded"
    )
    # Each open goal's vector is kept: the next check embeds the description alone.
    asked = len(gateway.asked)
    client.post("/v1/goals/similar", json={"description": COMPACTION + " And more."}, headers=OPERATOR)
    assert len(gateway.asked) == asked + 1 and len(gateway.asked[-1]) == 1
    # A goal whose description changed is embedded again.
    with db.connect(autocommit=True) as conn:
        conn.execute("UPDATE planner.goal SET statement = 'Login works with a passkey.' WHERE id = %s", (same["id"],))
    again = client.post("/v1/goals/similar", json={"description": COMPACTION}, headers=OPERATOR).json()
    assert again["goals"] == [] and len(gateway.asked[-1]) == 2


@pytest.mark.db
def test_a_short_description_is_not_compared_and_a_failed_check_says_why(world):
    client, _, gateway = world
    _goal(client, "Runs keep their task when context compaction happens, and compaction is cheaper.")
    asked = len(gateway.asked)

    short = client.post("/v1/goals/similar", json={"description": "compaction"}, headers=OPERATOR).json()

    assert short == {"goals": [], "cutoff": 0.7, "model": "openai:text-embedding-3-small", "checked": False}
    assert len(gateway.asked) == asked
    gateway.down = True
    failed = client.post("/v1/goals/similar", json={"description": COMPACTION}, headers=OPERATOR)
    assert failed.status_code == 502 and "the HTTP Gateway answered 400" in failed.json()["detail"]


@pytest.mark.db
def test_an_operators_answer_is_recorded_and_listed_for_setting_the_cutoff(world):
    client, events, _ = world
    same = _goal(client, "Runs keep their task when context compaction happens, and compaction is cheaper.")
    wrong = client.post(
        "/v1/goals/similar/answer", json={"goal_id": same["id"], "score": 0.9, "answer": "maybe"}, headers=OPERATOR
    )
    assert (
        wrong.status_code == 422 and "answer must be one of opened, not_duplicate, same_work, different" in wrong.text
    )

    said = client.post(
        "/v1/goals/similar/answer",
        json={"goal_id": same["id"], "score": 0.93, "answer": "same_work", "new_goal_id": "g-new"},
        headers=OPERATOR,
    )

    assert said.status_code == 201
    judged = [e[1] for e in events.events if e[0] == "goal_similarity_judged"]
    assert judged == [
        {
            "goal_id": same["id"],
            "score": 0.93,
            "answer": "same_work",
            "cutoff": 0.7,
            "model": "openai:text-embedding-3-small",
            "new_goal_id": "g-new",
        }
    ]


def test_the_settings_are_checked_and_a_saved_configuration_without_them_uses_the_deployments():
    assert similar_goals.check({}) == []
    assert (
        similar_goals.check({"similar_goals": {"enabled": True, "model": "voyage:voyage-4", "cutoff": 0.8, "most": 5}})
        == []
    )
    bad = similar_goals.check({"similar_goals": {"enabled": "yes", "model": "nope", "cutoff": 2, "most": 9}})
    assert [reason.split(" must")[0] for reason in bad] == [
        "similar_goals.enabled", "similar_goals.model", "similar_goals.cutoff", "similar_goals.most",
    ]  # fmt: skip
    assert similar_goals.settings()["model"] == "openai:text-embedding-3-small"
    similar_goals.check_settings()
    assert similar_goals.cosine([1.0, 0.0], [1.0, 0.0]) == 1.0 and similar_goals.cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert similar_goals.cosine([1.0], [1.0, 2.0]) == 0.0 and similar_goals.cosine([0.0], [0.0]) == 0.0
