"""A base branch that fails its standing checks gets one fix task, started by the goal's autonomy
(planner/base_fix.py)."""

import asyncio
import uuid

import pytest

from meta_harness import db
from meta_harness.planner import base_fix, needs
from meta_harness.run_service import code_host

from .test_planner import OPERATOR, migrated, registry  # noqa: F401
from .test_planning import REPO, _feature
from .test_planning import world  # noqa: F401

PYTEST = "uv run pytest -q"
OUTPUT = "FAILED tests/test_app.py::test_adds - assert 3 == 4\n1 failed, 9 passed"


@pytest.fixture
def repo():
    """A repo no other test's base fix is kept for: one row is kept for a repo."""
    name = f"davecthomas/base-{uuid.uuid4().hex[:8]}"
    yield name
    with db.connect(autocommit=True) as conn:
        conn.execute("DELETE FROM planner.base_fix WHERE repo = %s", (name,))


def _ran(events, goal: dict, repo: str, commit: str, passed: bool, command: str = PYTEST) -> None:
    events.emit(
        "base_check_ran",
        {
            "repo": repo,
            "commit": commit,
            "command": command,
            "passed": passed,
            "exit_code": 0 if passed else 1,
            "output": "" if passed else OUTPUT,
            "goal_id": goal["id"],
        },
        tags={"goal_id": goal["id"]},
    )


def _look(goal: dict, tracker, events) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    return asyncio.run(base_fix.look(goal, tracker, events, errors)), errors


def _goal(client, goal: dict) -> dict:
    return client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()


def _needs(tasks, goal: dict) -> list[dict]:
    """What the goal needs for its base, as the one needs list has it (the list itself holds every test's goals)."""
    return [n for n in needs.by_id(goal["id"], tasks)[1] if n["action"] == "fix_base"]


@pytest.mark.parametrize("autonomy", ["standard", "full"])
def test_a_goal_that_does_not_ask_starts_one_fix_task_for_a_failing_base(world, repo, autonomy):  # noqa: F811
    client, events, tracker, tasks = world
    goal = _goal(client, _feature(client, repos=[REPO], autonomy=autonomy))
    _ran(events, goal, repo, "b0", passed=False)

    started, errors = _look(goal, tracker, events)

    assert errors == [] and len(started) == 1
    [issue] = tracker.created
    assert issue.ref == started[0] and issue.repo == repo and goal["label"] in issue.labels
    assert issue.title == f"Fix the checks that fail on the base branch: {PYTEST}"
    assert "at commit b0" in issue.body and "FAILED tests/test_app.py::test_adds" in issue.body
    assert code_host.acceptance_checks(issue.body) == [PYTEST], "the failing check is what proves the fix"
    with db.connect(autocommit=True) as conn:
        link = conn.execute(
            "SELECT goal_id, planned_at IS NOT NULL FROM planner.issue_link WHERE ref = %s", (issue.ref,)
        ).fetchone()
    assert link == (goal["id"], True), "taken into the goal as it is, not held for its size"
    assert events.types().count("base_fix_started") == 1 and _needs(tasks, goal) == []

    # A second failing task on the same repo, and a second sweep, start no second fix task.
    _ran(events, goal, repo, "b0", passed=False, command="uv run ruff check .")
    assert _look(goal, tracker, events) == ([], []) and len(tracker.created) == 1


def test_a_goal_that_asks_lists_the_failing_base_and_starts_nothing_until_told(world, repo):  # noqa: F811
    client, events, tracker, tasks = world
    goal = _goal(client, _feature(client, repos=[REPO], autonomy="ask"))
    _ran(events, goal, repo, "b0", passed=False)

    assert _look(goal, tracker, events) == ([], []) and tracker.created == []

    [need] = _needs(tasks, goal)
    name = repo.rsplit("/", 1)[-1]
    assert need["asks"] == "Fix the base branch"
    assert need["what"].startswith(f"{name}'s base branch fails 1 check in the sandbox")
    started = client.post(f"/v1/goals/{goal['id']}/base-fix", headers=OPERATOR)
    assert started.status_code == 200, started.text
    [ref] = started.json()["started"]
    assert [i.ref for i in tracker.created] == [ref] and _needs(tasks, goal) == []
    again = client.post(f"/v1/goals/{goal['id']}/base-fix", headers=OPERATOR)
    assert again.status_code == 409 and "Reload the page" in again.json()["detail"]


def test_a_base_that_passes_again_is_no_longer_asked_about(world, repo):  # noqa: F811
    client, events, tracker, tasks = world
    goal = _goal(client, _feature(client, repos=[REPO], autonomy="ask"))
    _ran(events, goal, repo, "b0", passed=False)
    _look(goal, tracker, events)
    assert len(_needs(tasks, goal)) == 1

    _ran(events, goal, repo, "b1", passed=True)
    _look(goal, tracker, events)

    assert _needs(tasks, goal) == [] and tracker.created == []


def test_another_goal_reuses_the_fix_task_that_is_open_and_a_later_commit_gets_its_own(world, repo):  # noqa: F811
    client, events, tracker, _ = world
    finder = _goal(client, _feature(client, repos=[REPO], autonomy="full"))
    other = _goal(client, _feature(client, repos=[REPO], autonomy="full", statement="Add imports"))
    _ran(events, finder, repo, "b0", passed=False)
    [ref], _ = _look(finder, tracker, events)

    _ran(events, other, repo, "b1", passed=False)
    assert _look(other, tracker, events) == ([], []), "the open fix task is the repo's, whichever goal made it"

    # The fix merged and its issue closed, and the base still fails on a later commit: that one gets a task.
    with db.connect(autocommit=True) as conn:
        conn.execute("UPDATE planner.issue_link SET state = 'closed' WHERE ref = %s", (ref,))
    started, errors = _look(other, tracker, events)
    assert errors == [] and len(started) == 1 and len(tracker.created) == 2
    assert _look(other, tracker, events) == ([], []), "and that commit is fixed once"


def test_a_tracker_that_refuses_the_issue_is_reported_and_tried_again(world, repo):  # noqa: F811
    client, events, tracker, _ = world
    goal = _goal(client, _feature(client, repos=[REPO], autonomy="full"))
    _ran(events, goal, repo, "b0", passed=False)
    tracker.refuse_at = 1

    started, errors = _look(goal, tracker, events)

    assert started == [] and "could not be started" in errors[0]
    again, errors = _look(goal, tracker, events)
    assert len(again) == 1 and errors == []
