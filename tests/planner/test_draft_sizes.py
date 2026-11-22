"""A plan's new tasks are sized before the plan is shown, and the planner resizes the ones too large."""

import pytest
from fastapi.testclient import TestClient

from meta_harness.planner import draft_sizes
from meta_harness.planner.sizing import Sized

from .test_planner import OPERATOR, Events, FakeTasks, _no_title, migrated, registry  # noqa: F401
from .test_planning import WritingTracker, _feature, draft


class Sizer:
    """Says a task is large while its title is in `large`, and that one in `bare` lacks its checks."""

    def __init__(self):
        self.large: set[str] = set()
        self.bare: set[str] = set()
        self.asked: list[str] = []

    async def __call__(self, issue):
        self.asked.append(issue.title)
        if issue.title in self.large:
            return Sized(
                "large", reasons=["it changes the API and the store"], split=["The API", "The store"], model="m"
            )
        return Sized("small", missing=["acceptance_checks"] if issue.title in self.bare else [], model="m")


@pytest.fixture
def world():
    from meta_harness.planner.service import create_app

    events, tracker, tasks, sizer = Events(), WritingTracker(), FakeTasks(), Sizer()
    app = create_app(
        events,
        tracker,
        tasks,
        operator_keys={"op-key": "dave"},
        service_keys={},
        sweep_seconds=0,
        titler=_no_title,
        sizer=sizer,
    )
    with TestClient(app) as client:
        yield client, events, tasks, sizer


def _ends(client, events, tasks, goal: dict, drafts: list[dict]) -> dict:
    """The goal's running planning task ends having drafted these. Returns the plan the API then shows."""
    task = next(t for t in tasks.tasks.values() if t.get("goal_id") == goal["id"] and t["status"] == "queued")
    for d in drafts:
        events.emit("issue_drafted", d, task_id=task["id"])
    task["status"] = "closed"
    return client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()


def _plan_tasks(tasks, goal: dict) -> list[dict]:
    return [t for t in tasks.tasks.values() if t.get("goal_id") == goal["id"] and t["task_type"] == "plan"]


def test_a_task_too_large_for_one_pull_request_sends_the_planner_back_before_anyone_approves(world):
    """A plan's own tasks used to skip the sizer: one written too large ran until it was out of turns, and
    then had to be split."""
    client, events, tasks, sizer = world
    goal = _feature(client)
    sizer.large = {"Build api"}

    first = _ends(client, events, tasks, goal, [draft("store"), draft("api", ("store",))])

    assert sizer.asked == ["Build store", "Build api"], "each new task is sized, as an issue is before it runs"
    [why] = first["plan"]["invalid"]
    assert why.startswith("the task api is too large for one pull request: it changes the API and the store")
    assert "Split it into smaller tasks, or ask for a new plan" in why
    # The planner is sent back by itself, told which task and how it could split.
    assert first["planning_task_id"], "the goal has a planning task again: its page says the planner is working"
    [_, again] = _plan_tasks(tasks, goal)
    assert again["external_ref"].endswith(":resize1")
    assert "- api (Build api) is too large for one pull request" in again["description"]
    assert "It could split into: The API; The store." in again["description"]
    assert "Keep every task that is not named here as it is" in again["description"]
    assert client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR).status_code == 409

    resized = _ends(client, events, tasks, goal, [draft("store"), draft("api-routes", ("store",)), draft("api-models")])

    assert resized["plan"]["invalid"] == [] and resized["planning_task_id"] is None
    assert len(_plan_tasks(tasks, goal)) == 2, "sized right, the planner is not sent back again"
    sized = [e for e in events.events if e[0] == "issue_sized"]
    assert len(sized) == 5 and sized[0][1]["ref"] == f"draft:{goal['id']}:store", (
        "each sizing is recorded, with its cost"
    )


def test_the_planner_is_sent_back_a_bounded_number_of_times_and_then_the_plan_says_what_is_wrong(world):
    client, events, tasks, sizer = world
    # Strict about its issues: a task that lacks what a run needs is sent back too, not only one too large.
    goal = _feature(client, strict_issues=True)
    sizer.large = {"Build api"}
    sizer.bare = {"Build ui"}

    for _ in range(draft_sizes.DEFAULT_ROUNDS + 1):
        shown = _ends(client, events, tasks, goal, [draft("api"), draft("ui", ("api",))])

    assert len(_plan_tasks(tasks, goal)) == draft_sizes.DEFAULT_ROUNDS + 1, "the first plan, then its resize rounds"
    assert shown["planning_task_id"] is None, "after them the plan is the operator's, with what is wrong said"
    assert shown["plan"]["invalid"] == [
        "the task api is too large for one pull request: it changes the API and the store. Split it into smaller "
        "tasks, or ask for a new plan",
        "the task ui lacks acceptance checks. Edit it to add what it lacks, or ask for a new plan",
    ]


def test_a_plan_whose_tasks_are_all_one_runs_work_is_approvable_at_once(world):
    client, events, tasks, sizer = world
    goal = _feature(client)
    # What a task lacks stops it only in a strict goal, as it does an issue the sweep takes in.
    sizer.bare = {"Build api"}

    shown = _ends(client, events, tasks, goal, [draft("store"), draft("api", ("store",))])

    assert shown["plan"]["invalid"] == [] and shown["planning_task_id"] is None
    assert len(_plan_tasks(tasks, goal)) == 1
    assert client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR).status_code == 200
