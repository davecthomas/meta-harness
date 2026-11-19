"""The planner names tasks that have no short name: a goal's batch in one model call."""

import asyncio
import json

import httpx
import pytest

from meta_harness.planner import naming


class Tasks:
    def __init__(self, tasks: list[dict], taken: dict[str, list[str]] | None = None) -> None:
        self.tasks, self.taken, self.named = tasks, taken or {}, {}

    def unnamed(self) -> dict:
        return {"tasks": [t for t in self.tasks if t["id"] not in self.named], "taken": self.taken}

    def set_name(self, task_id: str, name: str) -> dict:
        self.named[task_id] = name
        return {"id": task_id, "name": name}


class Events:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type, payload=None, **fields):
        self.events.append((event_type, payload or {}))
        return "e"


@pytest.fixture(autouse=True)
def fresh():
    naming._failed_at.clear()


def _task(task_id: str, goal_id: str | None = "g-1", title: str = "Help text is too dim") -> dict:
    return {"id": task_id, "goal_id": goal_id, "title": title, "task_type": "bug"}


def test_each_goals_unnamed_tasks_are_named_in_one_call_that_is_told_the_names_taken():
    tasks = Tasks([_task("t-1"), _task("t-2"), _task("t-3", "g-2"), _task("t-4", None)], {"g-1": ["Merge wait fix"]})
    events, calls = Events(), []

    async def namer(batch, taken):
        calls.append(([t["id"] for t in batch], taken))
        return {t["id"]: f"Name {t['id'][-1]}" for t in batch}, "claude:test-model", "0.0004"

    named = asyncio.run(naming.name_unnamed(tasks, events, namer))

    assert named == 4 and tasks.named == {"t-1": "Name 1", "t-2": "Name 2", "t-3": "Name 3", "t-4": "Name 4"}
    assert calls == [(["t-1", "t-2"], ["Merge wait fix"]), (["t-3"], []), (["t-4"], [])]
    assert events.events[0] == (
        "task_names_written",
        {"goal_id": "g-1", "tasks": 2, "model": "claude:test-model", "usd_cost": "0.0004"},
    )
    assert events.events[2][1]["goal_id"] == ""


def test_a_name_too_long_is_cut_to_fit_and_an_empty_one_leaves_the_task_for_the_next_try():
    tasks = Tasks([_task("t-1"), _task("t-2")])

    async def namer(batch, taken):
        return {"t-1": "The very long descriptive name of this particular task", "t-2": " "}, "claude:m", "0"

    assert asyncio.run(naming.name_unnamed(tasks, Events(), namer)) == 1
    assert tasks.named == {"t-1": "The very long descriptive"}
    assert naming.fit("Hint text contrast.") == "Hint text contrast"
    assert naming.fit("Supercalifragilisticexpialidocious-name") is None


def test_a_call_that_fails_is_logged_with_what_happens_next_and_not_asked_again_at_once(caplog):
    tasks = Tasks([_task("t-1"), _task("t-2", "g-2")])
    asked = []

    async def namer(batch, taken):
        asked.append(batch[0]["id"])
        if batch[0]["id"] == "t-1":
            raise naming.NamingError("the HTTP Gateway answered 503: busy")
        return {"t-2": "Second one"}, "claude:m", "0"

    assert asyncio.run(naming.name_unnamed(tasks, Events(), namer)) == 1
    assert asyncio.run(naming.name_unnamed(tasks, Events(), namer)) == 0

    # The other goal was still named, and the failed one is not asked about again until its wait is over.
    assert asked == ["t-1", "t-2"] and tasks.named == {"t-2": "Second one"}
    assert "could not name 1 tasks of goal g-1" in caplog.text and "show their titles until the next try" in caplog.text


def test_the_model_call_sends_titles_and_names_taken_and_keeps_only_the_tasks_asked_about(monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", "http://gateway")
    monkeypatch.setenv("GATEWAY_API_KEY", "k")
    monkeypatch.setattr(naming.updates, "settings", lambda: {"model": "claude:test-model"})
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        data = {"names": [{"id": "t-1", "name": "Hint text contrast"}, {"id": "t-9", "name": "Not asked"}]}
        return httpx.Response(200, json={"finish_reason": "complete", "data": data, "usd_cost": "0.0003"})

    names, model, cost = asyncio.run(naming.write([_task("t-1")], ["Merge wait fix"], httpx.MockTransport(handler)))

    assert names == {"t-1": "Hint text contrast"} and (model, cost) == ("claude:test-model", "0.0003")
    assert sent["engine"] == "claude" and sent["model"] == "test-model"
    assert '"Help text is too dim"' in sent["prompt"] and "Merge wait fix" in sent["prompt"]


def test_without_a_gateway_the_error_says_which_settings_to_set(monkeypatch):
    monkeypatch.delenv("GATEWAY_URL", raising=False)
    monkeypatch.setattr(naming.updates, "settings", lambda: {"model": "claude:test-model"})
    with pytest.raises(naming.NamingError, match="set GATEWAY_URL and GATEWAY_API_KEY"):
        asyncio.run(naming.write([_task("t-1")], []))
