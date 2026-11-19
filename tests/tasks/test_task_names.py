"""A task's short name: the planner sets it, a goal's names stay apart, and a search finds a task by it."""

import uuid

import pytest
from fastapi.testclient import TestClient

from meta_harness import db, migrations
from meta_harness.tasks.service import create_app

pytestmark = pytest.mark.db

CALLER = {"Authorization": "Bearer caller-key"}
SERVICE = {"Authorization": "Bearer service-key"}


class Events:
    def emit(self, event_type, payload=None, **fields):
        return str(uuid.uuid4())


@pytest.fixture(scope="module", autouse=True)
def migrated():
    try:
        with db.connect() as conn:
            migrations.apply(conn)
            conn.commit()
    except Exception as err:  # noqa: BLE001
        pytest.skip(f"no Postgres at {db.database_url()}: {err}")


@pytest.fixture
def api():
    app = create_app(Events(), caller_keys={"caller-key": "ops"}, service_keys={"service-key": "planner"})
    with TestClient(app) as client:
        yield client


def _create(api, goal_id: str | None = None, title: str = "Help text is too dim to read") -> dict:
    body = {
        "external_ref": f"ref-{uuid.uuid4().hex[:8]}",
        "repo": "davecthomas/meta-harness",
        "task_type": "bug",
        "title": title,
        "description": "d",
        "acceptance_checks": ["uv run pytest -q"],
        "goal_id": goal_id,
    }
    response = api.post("/v1/tasks", json=body, headers=SERVICE)
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_a_new_task_has_no_name_until_the_planner_gives_it_one(api):
    task = _create(api)
    assert task["name"] is None

    named = api.post(f"/v1/tasks/{task['id']}/name", json={"name": ' "Hint text contrast." '}, headers=SERVICE)

    assert named.status_code == 200 and named.json()["name"] == "Hint text contrast"
    assert api.get(f"/v1/tasks/{task['id']}", headers=SERVICE).json()["name"] == "Hint text contrast"


def test_a_name_another_task_of_the_goal_has_gets_a_number(api):
    goal = f"g-{uuid.uuid4().hex[:12]}"
    first, second, other = _create(api, goal), _create(api, goal), _create(api, f"g-{uuid.uuid4().hex[:12]}")

    names = [
        api.post(f"/v1/tasks/{t['id']}/name", json={"name": "Hint text contrast"}, headers=SERVICE).json()["name"]
        for t in (first, second, other)
    ]

    assert names == ["Hint text contrast", "Hint text contrast 2", "Hint text contrast"]


def test_a_name_too_long_to_stand_where_an_id_did_is_refused_with_the_limit(api):
    task = _create(api)

    long = api.post(f"/v1/tasks/{task['id']}/name", json={"name": "one two three four five"}, headers=SERVICE)
    empty = api.post(f"/v1/tasks/{task['id']}/name", json={"name": " . "}, headers=SERVICE)

    assert long.status_code == 422 and "at most 4 words and 28 characters" in long.json()["detail"]
    assert empty.status_code == 422 and "send two to four" in empty.json()["detail"]


def test_only_a_service_names_a_task(api):
    task = _create(api)
    assert api.post(f"/v1/tasks/{task['id']}/name", json={"name": "Mine"}, headers=CALLER).status_code == 403
    assert api.get("/v1/tasks/unnamed", headers=CALLER).status_code == 403


def test_the_unnamed_list_holds_tasks_with_no_name_and_the_names_their_goals_have(api):
    goal = f"g-{uuid.uuid4().hex[:12]}"
    named, waiting = _create(api, goal), _create(api, goal, title="Router tiers pick from a list")
    api.post(f"/v1/tasks/{named['id']}/name", json={"name": "Hint text contrast"}, headers=SERVICE)

    found = api.get("/v1/tasks/unnamed", params={"limit": 200}, headers=SERVICE).json()

    ids = {t["id"]: t for t in found["tasks"]}
    assert waiting["id"] in ids and named["id"] not in ids
    assert ids[waiting["id"]]["title"] == "Router tiers pick from a list"
    assert found["taken"][goal] == ["Hint text contrast"]


def test_a_search_finds_a_task_by_its_name(api):
    task = _create(api)
    name = f"Zeta {uuid.uuid4().hex[:6]}"
    api.post(f"/v1/tasks/{task['id']}/name", json={"name": name}, headers=SERVICE)

    found = api.get("/v1/tasks", params={"q": name.lower()}, headers=SERVICE).json()["tasks"]

    assert [t["id"] for t in found] == [task["id"]]
