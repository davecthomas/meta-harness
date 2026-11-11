"""The scheduler's record of why each queued task waits, and the Task API's `held` that serves it."""

import pytest
from fastapi.testclient import TestClient

from meta_harness import db
from meta_harness.scheduler import holds
from meta_harness.scheduler.admission import set_kill_switch
from meta_harness.tasks.service import create_app

from .test_admission import Events, Goals, Tasks, _admission, migrated, repo  # noqa: F401

pytestmark = pytest.mark.db


def _hold(task_id: str) -> dict | None:
    with db.connect(autocommit=True) as conn:
        return holds.get(conn, task_id)


async def test_each_held_task_has_a_kind_and_what_it_names(repo):  # noqa: F811
    tasks, goals = Tasks(), Goals()
    goals.goals["g-paused"] = {"status": "paused", "max_usd": None}
    goals.goals["g-stopped"] = {"status": "budget_exhausted", "max_usd": "1.00"}
    goals.goals["g-one"] = {"status": "active", "max_usd": None, "max_parallel": 1}
    first = tasks.add(repo, paths=["src/api"], goal_id="g-one")
    second = tasks.add(repo, paths=["docs"], goal_id="g-one")
    area = tasks.add(repo, paths=["src/api/tasks.py"])
    paused = tasks.add(f"{repo}-b", goal_id="g-paused")
    stopped = tasks.add(f"{repo}-c", goal_id="g-stopped")
    admission, started = _admission(tasks, goals)

    await admission.pass_once()
    # The goal's running count is read from the locks at the next pass.
    result = await admission.pass_once()

    assert started == [first]
    found = {task_id: _hold(task_id) for task_id in (second, area, paused, stopped)}
    assert {task_id: hold["kind"] for task_id, hold in found.items()} == {
        second: "goal_parallel",
        area: "repo_area",
        paused: "goal_status",
        stopped: "goal_budget",
    }
    assert found[second]["detail"] == {"goal_id": "g-one", "holders": [first]}
    assert found[area]["detail"] == {"holders": [first], "paths": ["src/api/tasks.py"], "held_paths": ["src/api"]}
    assert found[paused]["detail"] == {"goal_id": "g-paused", "status": "paused"}
    assert found[stopped]["reason"] == "goal g-stopped is stopped by its budget" == result.held[stopped]
    assert _hold(first) is None, "a started task waits no longer"


async def test_a_task_past_the_systems_limit_names_the_tasks_holding_the_places(repo):  # noqa: F811
    tasks = Tasks()
    first, second = tasks.add(repo, paths=["a"]), tasks.add(repo, paths=["b"])
    admission, _ = _admission(tasks, max_running=1)

    await admission.pass_once()

    hold = _hold(second)
    assert hold["kind"] == "system_parallel" and hold["detail"] == {"holders": [first]}
    assert hold["reason"] == "the system already runs 1 tasks, its limit"


async def test_a_hold_is_recorded_once_and_survives_a_restart(repo):  # noqa: F811
    tasks, goals, events = Tasks(), Goals(), Events()
    goals.goals["g-paused"] = {"status": "paused", "max_usd": None}
    waiting = tasks.add(repo, goal_id="g-paused")
    admission, _ = _admission(tasks, goals, events)
    await admission.pass_once()
    began = _hold(waiting)
    await admission.pass_once()

    # A new scheduler, as after a deploy: the reason is the same, so nothing is recorded again.
    restarted, started = _admission(tasks, goals, events)
    await restarted.pass_once()

    held = [(payload, task_id) for kind, payload, task_id in events.emitted if kind == "task_held"]
    assert held == [({"reason": "goal g-paused is paused", "kind": "goal_status"}, waiting)]
    again = _hold(waiting)
    assert again["since"] == began["since"] and again["last_checked"] > began["last_checked"]

    goals.goals["g-paused"]["status"] = "active"
    await restarted.pass_once()
    assert started == [waiting] and _hold(waiting) is None


async def test_the_kill_switch_holds_every_queued_task(repo):  # noqa: F811
    tasks = Tasks()
    queued = tasks.add(repo)
    admission, _ = _admission(tasks)
    set_kill_switch(True, "test", "stop everything")
    try:
        result = await admission.pass_once()
    finally:
        set_kill_switch(False, "test", "lifted")
    assert _hold(queued)["kind"] == "kill_switch" and "kill switch is on" in result.held[queued]


def test_the_task_api_serves_a_queued_tasks_hold(repo):  # noqa: F811
    class Recorded:
        def emit(self, event_type, payload=None, **fields):
            return "id"

    app = create_app(Recorded(), caller_keys={"caller-key": "ops"}, service_keys={"service-key": "scheduler"})
    as_caller = {"Authorization": "Bearer caller-key"}
    with TestClient(app) as api:
        body = {
            "external_ref": f"hold-{repo}",
            "repo": repo,
            "task_type": "bug",
            "title": "Fix it",
            "description": "It is broken.",
            "acceptance_checks": ["make test"],
        }
        task = api.post("/v1/tasks", json=body, headers=as_caller).json()
        assert api.get(f"/v1/tasks/{task['id']}", headers=as_caller).json()["held"] is None

        holds.save(
            {task["id"]: holds.Hold("goal_status", "goal g-1 is paused", {"goal_id": "g-1", "status": "paused"})}
        )
        try:
            held = api.get(f"/v1/tasks/{task['id']}", headers=as_caller).json()["held"]
            assert (held["kind"], held["reason"]) == ("goal_status", "goal g-1 is paused")
            assert held["detail"] == {"goal_id": "g-1", "status": "paused"} and held["since"] and held["last_checked"]
            listed = api.get("/v1/tasks", params={"q": task["id"]}, headers=as_caller).json()["tasks"]
            assert [t["held"]["kind"] for t in listed] == ["goal_status"]
        finally:
            holds.save({})
