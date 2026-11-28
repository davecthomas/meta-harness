"""Each repo's health, as the console's Repos page reads it (planner/repo_health.py)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from meta_harness import db
from meta_harness.planner import repos
from meta_harness.run_service import code_host

from .test_planner import OPERATOR, migrated  # noqa: F401
from .test_planning import _feature
from .test_planning import world  # noqa: F401


@pytest.fixture
def named(monkeypatch):
    """Two repos no other test's goals name, one in the registry with a deploy check, and their usual check
    times. The page lists every open goal's repos, so the tests look only at these."""
    tag = uuid.uuid4().hex[:8]
    red, green = f"acme/red-{tag}", f"acme/green-{tag}"
    registry = {green.lower(): repos.Repo(green, "github", ["uv run pytest -q"], "deploy")}
    monkeypatch.setattr(repos, "registry", lambda: registry)
    monkeypatch.setattr(repos, "get", lambda name: registry.get(name.lower()) or repos.Repo(name, "github", []))

    async def check_details(repo, ref):
        return [{"name": "tests", "seconds": 300}, {"name": "lint", "seconds": 20}] if repo == green else []

    monkeypatch.setattr(code_host, "check_details", check_details)
    yield red, green
    with db.connect(autocommit=True) as conn:
        conn.execute("DELETE FROM planner.base_fix WHERE repo = ANY(%s)", ([red.lower(), green.lower()],))


def _health(client, *names: str) -> dict[str, dict]:
    found = client.get("/v1/repos/health", headers=OPERATOR).json()
    return {r["repo"]: r for r in found["repos"] if r["repo"] in names}


def _base(events, repo: str, goal: dict, passed: bool, command: str = "npm test", source: str = "") -> None:
    payload = {"repo": repo, "commit": "4c1e9d0", "command": command, "passed": passed, "goal_id": goal["id"]}
    events.emit("base_check_ran", {**payload, **({"source": source} if source else {})}, tags={"goal_id": goal["id"]})


def test_a_repo_says_how_its_base_stands_the_work_there_and_its_settings(world, named):  # noqa: F811
    client, events, _, tasks = world
    red, green = named
    goal = _feature(client, repos=[red], statement="Add CSV export")
    _base(events, red, goal, passed=False)
    _base(events, red, goal, passed=True, command="npm run lint")
    other = _feature(client, repos=[green], statement="Tidy the docs")
    _base(events, green, other, passed=True, command="uv run pytest -q", source="code_host")
    tasks.tasks["t-run"] = {
        "id": "t-run",
        "goal_id": goal["id"],
        "repo": red,
        "status": "running",
        "pr_url": "https://x/pull/1",
    }
    tasks.tasks["t-done"] = {
        "id": "t-done",
        "goal_id": goal["id"],
        "repo": red,
        "status": "closed",
        "pr_url": "https://x/pull/2",
    }

    found = _health(client, red, green)

    failing = found[red]
    assert failing["base"]["state"] == "failing" and failing["base"]["failing"] == ["npm test"]
    assert (failing["base"]["commit"], failing["base"]["found_by"]) == (
        "4c1e9d0",
        goal.get("title") or "Add CSV export",
    )
    assert (failing["registered"], failing["default_checks"], failing["deploy_check"]) == (False, [], "")
    assert [g["id"] for g in failing["goals"]] == [goal["id"]]
    assert (failing["tasks"], failing["pulls"]) == (1, 1), "a closed task is no longer work there"
    passing = found[green]
    assert passing["base"]["state"] == "passing"
    assert passing["base"]["checks"] == [{"command": "uv run pytest -q", "passed": True, "on": "code_host"}]
    assert (passing["registered"], passing["deploy_check"], passing["usual_seconds"]) == (True, "deploy", 300)
    # A failing base comes first, whatever the names.
    listed = [r["repo"] for r in client.get("/v1/repos/health", headers=OPERATOR).json()["repos"]]
    assert listed.index(red) < listed.index(green)


def test_a_repo_in_the_registry_with_no_goal_is_listed_as_not_checked_yet(world, named):  # noqa: F811
    client, _, _, _ = world
    _, green = named

    idle = _health(client, green)[green]

    assert idle["base"] is None and idle["goals"] == [] and idle["tasks"] == 0 and idle["live"] is None
    assert idle["default_checks"] == ["uv run pytest -q"]


def test_checks_rerun_by_chance_and_the_last_change_that_went_live_are_counted_for_their_repo(world, named):  # noqa: F811
    client, events, _, tasks = world
    _, green = named
    goal = _feature(client, repos=[green])
    tasks.tasks["t-1"] = {
        "id": "t-1",
        "goal_id": goal["id"],
        "repo": green,
        "status": "closed",
        "title": "Add the answer field",
    }
    now = datetime.now(UTC)
    rerun = {"remedy": "rerun_checks", "because": "tests, lint failed on 1a2b3c4"}
    events.emit("remedy_applied", rerun, task_id="t-1", time_utc=now.isoformat())
    events.emit(
        "remedy_applied", {**rerun, "because": "tests failed on 5d6e7f8"}, task_id="t-1", time_utc=now.isoformat()
    )
    events.emit("remedy_applied", rerun, task_id="t-1", time_utc=(now - timedelta(days=9)).isoformat())
    events.emit("remedy_applied", {"remedy": "stronger", "because": "tests failed on 0000000"}, task_id="t-1")
    events.emit(
        "merge_checks",
        {"state": "passed", "deploy_check": "deploy", "live": True},
        task_id="t-1",
        time_utc=now.isoformat(),
    )
    events.emit("merge_checks", {"state": "passed"}, task_id="t-1")

    found = _health(client, green)[green]

    assert found["reruns"] == [{"check": "lint", "times": 1}, {"check": "tests", "times": 2}], "the last seven days"
    assert (found["live"]["task_id"], found["live"]["title"], found["live"]["deploy_check"]) == (
        "t-1",
        "Add the answer field",
        "deploy",
    )


def test_a_failing_base_says_whether_its_fix_is_asked_about_or_under_way(world, named):  # noqa: F811
    import asyncio

    from meta_harness.planner import base_fix

    client, events, tracker, _ = world
    red, _ = named
    asking = client.get(f"/v1/goals/{_feature(client, repos=[red], autonomy='ask')['id']}", headers=OPERATOR).json()
    _base(events, red, asking, passed=False)
    asyncio.run(base_fix.look(asking, tracker, events, []))

    assert _health(client, red)[red]["fix"] == {
        "asked": True,
        "goal_id": asking["id"],
        "ref": "",
        "task_id": "",
        "open": True,
    }

    started = client.post(f"/v1/goals/{asking['id']}/base-fix", headers=OPERATOR).json()["started"]
    fix = _health(client, red)[red]["fix"]
    assert (fix["asked"], fix["ref"], fix["open"]) == (False, started[0], True)
    assert client.get("/v1/repos/health", headers=OPERATOR).json()["failing"] >= 1
