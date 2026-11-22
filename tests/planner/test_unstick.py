"""One action that gets a goal's stuck tasks moving (GET and POST /v1/goals/{id}/unstick)."""

import pytest
from fastapi.testclient import TestClient

from meta_harness import db
from meta_harness.planner import unstick_api
from meta_harness.supervisor import standing
from meta_harness.supervisor.rules import Decision
from meta_harness.tasks.client import TaskApiError

from .test_planner import (  # noqa: F401 - migrated and registry are fixtures
    OPERATOR,
    Events,
    FakeTasks,
    FakeTracker,
    _goal,
    _label,
    _no_title,
    migrated,
    registry,
)


class Tasks(FakeTasks):
    """The Task API's operator actions, recorded, with one task it refuses."""

    def __init__(self):
        super().__init__()
        self.took: list[tuple] = []
        self.refuses: str = ""

    def _take(self, task_id, *what):
        if task_id == self.refuses:
            raise TaskApiError(409, "a queued task is not waiting with a pull request open")
        self.took.append((task_id, *what))
        return self.tasks[task_id]

    def merge(self, task_id, actor=None):
        return self._take(task_id, "merge", actor)

    def retry(self, task_id, max_usd=None, actor=None, complexity=None):
        return self._take(task_id, "retry", actor, complexity)

    def restart(self, task_id, max_usd=None, actor=None):
        return self._take(task_id, "restart", actor)

    def budget(self, task_id, action, max_usd=None, actor=None):
        return self._take(task_id, "budget", action, max_usd, actor)


@pytest.fixture
def world():
    from meta_harness.planner.service import create_app

    tasks = Tasks()
    app = create_app(
        Events(),
        FakeTracker(),
        tasks,
        operator_keys={"op-key": "dave"},
        service_keys={},
        sweep_seconds=0,
        titler=_no_title,
    )
    with TestClient(app) as client:
        yield client, tasks


def _task(tasks: Tasks, goal_id: str, name: str, owner: str = "operator", moves: tuple = (), **fields) -> str:
    task = tasks.create(external_ref=f"ref-{goal_id}-{name}", goal_id=goal_id, title=name, name=name)
    task.update(fields)
    with db.connect(autocommit=True) as conn:
        standing.save(conn, task["id"], Decision(owner, "stuck", f"{name} is stuck", ask="Do something.", moves=moves))
    return task["id"]


def test_each_stuck_task_takes_its_first_recommended_step_and_the_rest_are_left_with_why(world):
    client, tasks = world
    goal_id = _goal(client, _label())["id"]
    merge = _task(tasks, goal_id, "clean", moves=("merge", "cancel"))
    over = _task(tasks, goal_id, "conflicted", moves=("start_over", "retry", "cancel"))
    stronger = _task(tasks, goal_id, "unresolved", moves=("retry_stronger", "retry", "merge_as_is"))
    person = _task(tasks, goal_id, "needs approval", moves=("open_pull_request", "merge"))
    asks = {"for": "task", "to_usd": "9.00", "add_usd": "1", "over_ceiling": False}
    raised = _task(tasks, goal_id, "short", waiting_on="budget", budget_request=asks)
    capped = _task(tasks, goal_id, "capped", waiting_on="budget", budget_request=asks | {"over_ceiling": True})
    for_goal = _task(tasks, goal_id, "goal budget", waiting_on="budget", budget_request={"for": "goal"})
    _task(tasks, goal_id, "working", owner="workflow")
    _task(tasks, goal_id, "shepherd has it", owner="supervisor")

    would = client.get(f"/v1/goals/{goal_id}/unstick", headers=OPERATOR).json()

    assert tasks.took == [], "asking what it would do does nothing"
    assert {(m["task_id"], m["step"]) for m in would["moves"]} == {
        (merge, "merge"),
        (over, "start_over"),
        (stronger, "retry_stronger"),
        (raised, "raise"),
        (capped, "end_hold"),
    }
    assert {left["task_id"]: left["why"] for left in would["left"]} == {
        person: "its pull request needs something only a person gives on the code host",
        for_goal: "it waits on its goal's budget: raise the goal's budget on its page",
    }
    assert {m["name"]: m["does"] for m in would["moves"]}["conflicted"] == "starts it over on the current base"

    did = client.post(f"/v1/goals/{goal_id}/unstick", headers=OPERATOR).json()

    assert sorted(tasks.took) == sorted(
        [
            (merge, "merge", "dave"),
            (over, "restart", "dave"),
            (stronger, "retry", "dave", "high"),
            (raised, "budget", "raise", "9.00", "dave"),
            (capped, "budget", "end", None, "dave"),
        ]
    ), "each under the operator's name"
    assert len(did["done"]) == 5 and did["failed"] == [] and len(did["left"]) == 2


def test_one_tasks_refusal_is_said_and_does_not_stop_the_others(world):
    client, tasks = world
    goal_id = _goal(client, _label())["id"]
    first = _task(tasks, goal_id, "moved on", moves=("merge",))
    second = _task(tasks, goal_id, "still stuck", moves=("retry",))
    tasks.refuses = first

    did = client.post(f"/v1/goals/{goal_id}/unstick", headers=OPERATOR).json()

    assert [(m["task_id"], m["step"]) for m in did["done"]] == [(second, "retry")]
    assert did["failed"][0]["task_id"] == first and "the Task API refused it" in did["failed"][0]["why"]


def test_a_task_with_no_move_is_left_with_what_its_standing_asks(world):
    client, tasks = world
    goal_id = _goal(client, _label())["id"]
    bare = _task(tasks, goal_id, "no moves")

    would = client.get(f"/v1/goals/{goal_id}/unstick", headers=OPERATOR).json()

    assert would == {"moves": [], "left": [{"task_id": bare, "name": "no moves", "why": "Do something."}]}
    assert client.get("/v1/goals/g-none/unstick", headers=OPERATOR).status_code == 404
    assert unstick_api.step_for({"id": "t", "standing": None}) == ("", "")
