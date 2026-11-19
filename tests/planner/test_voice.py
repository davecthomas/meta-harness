"""The planner's voice: its tone and how much technical detail it shares with the operator."""

import httpx

from meta_harness.config import kinds
from meta_harness.planner import planning, updates, voice

GOAL = {"id": "g-1", "title": "Exports", "statement": "Add exports.", "repos": ["o/r"]}


def test_the_planner_is_casual_and_shares_little_detail_unless_told_otherwise():
    assert voice.of({}) == {"tone": "casual", "detail": "low"}
    assert voice.of(planning.settings()) == {"tone": "casual", "detail": "low"}, "as shipped"
    assert voice.of({"voice": {"tone": "formal"}}) == {"tone": "formal", "detail": "low"}

    said = voice.instructions({}, "update")
    assert "friendly and casual" in said and "Do not name files, paths, functions, routes, commands" in said
    assert "one or two short sentences" in said


def test_each_setting_changes_what_the_model_is_told():
    formal = voice.instructions({"voice": {"tone": "formal", "detail": "high"}}, "plan")
    assert "formal and precise" in formal and "Name the files, functions, routes and commands" in formal
    assert "up to six sentences" in formal
    balanced = voice.instructions({"voice": {"tone": "balanced", "detail": "medium"}}, "update")
    assert "plain and professional" in balanced and "two or three sentences" in balanced


def test_a_voice_the_planner_does_not_have_is_refused_with_its_choices():
    shipped = planning.settings()
    assert kinds.check_planning(shipped, None) == []
    assert kinds.check_planning({**shipped, "voice": {"tone": "chatty", "detail": "none"}}, None) == [
        "voice.tone must be one of casual, balanced, formal: how the planner sounds to the operator",
        "voice.detail must be one of low, medium, high: how much technical detail the planner shares",
    ]
    assert kinds.check_planning({**shipped, "voice": "casual"}, None)[0].startswith("voice must be an object")
    assert kinds.check_planning({k: v for k, v in shipped.items() if k != "voice"}, None) == []


def test_the_planning_task_writes_its_summary_for_the_operator_in_the_planners_voice(monkeypatch):
    asked = planning.prompt(GOAL, None, None)
    assert "The summary and the gaps are the only part of the plan written for the operator" in asked
    assert "friendly and casual" in asked and "at most three gaps" in asked

    shipped = planning.settings()
    monkeypatch.setattr(planning, "settings", lambda: {**shipped, "voice": {"tone": "formal", "detail": "high"}})
    assert "formal and precise" in planning.prompt(GOAL, None, None)


async def test_an_update_is_written_in_the_planners_voice(monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", "http://gateway.test")
    monkeypatch.setenv("GATEWAY_API_KEY", "k")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        data = {"headline": "All good.", "body": "Two tasks are under way.", "needs_you": [], "next": ""}
        return httpx.Response(200, json={"data": data, "finish_reason": "complete", "usd_cost": "0.001"})

    found = {"goal": {"status": "active"}, "plan": None, "issues": []}
    await updates.write_update(found, None, httpx.MockTransport(handler))

    assert "friendly and casual" in seen[0]["system_prompt"] and "Do not name files" in seen[0]["system_prompt"]
    assert "Say only what the facts say" in seen[0]["system_prompt"]
