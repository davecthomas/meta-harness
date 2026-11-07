from fastapi.testclient import TestClient

from meta_harness.console.app import create_app
from meta_harness.tasks.client import TaskApiError


class Goals:
    def __init__(self):
        self.goals = {}
        self.calls = []

    def repos(self):
        return [{"name": "davecthomas/meta-harness", "tracker": "github"}]

    def list(self, statuses=None):
        return list(self.goals.values())

    def create(self, actor=None, **goal):
        if goal["label"] == "bad":
            raise TaskApiError(422, "not in the repo registry: x/y")
        self.calls.append(("create", actor))
        g = {
            "id": "g-1",
            "status": "active",
            "modality": "fix",
            "created_by": actor,
            "created_at": "2026-11-07T20:00:00",
            "issues": [
                {
                    "ref": "github:davecthomas/meta-harness#5",
                    "task_id": "t-1",
                    "state": "open",
                    "depends_on": [],
                    "refused_reason": None,
                }
            ],
            **goal,
        }
        self.goals[g["id"]] = g
        return g

    def get(self, goal_id):
        if goal_id not in self.goals:
            raise TaskApiError(404, "no such goal")
        return dict(self.goals[goal_id])

    def action(self, goal_id, name, actor=None):
        self.calls.append((name, actor))
        return self.goals[goal_id]

    def patch(self, goal_id, actor=None, **fields):
        self.calls.append(("patch", actor, fields))
        return self.goals[goal_id]


class Tasks:
    def list(self, statuses, goal_id, limit):
        return [
            {
                "id": "t-1",
                "status": "ready",
                "external_ref": "github:davecthomas/meta-harness#5",
                "goal_id": "g-1",
                "pr_url": "https://github.com/davecthomas/meta-harness/pull/9",
                "status_reason": None,
            }
        ]

    def get(self, task_id):
        return {"id": task_id, "status": "ready", "pr_url": "https://github.com/davecthomas/meta-harness/pull/9"}


def _client(identity="dave@example.com", operators=("dave@example.com",)):
    goals = Goals()
    app = create_app(goals, Tasks(), operators=set(operators), dev_identity=identity)
    return TestClient(app), goals


def test_an_identity_outside_the_access_list_gets_403():
    client, _ = _client(identity="someone@example.com")
    response = client.get("/")
    assert response.status_code == 403
    assert "not on the console" in response.text


def test_no_identity_gets_401():
    client, _ = _client(identity="")
    assert client.get("/").status_code == 401


def test_creating_a_goal_names_the_operator_and_shows_the_goal():
    client, goals = _client()
    response = client.post(
        "/goals",
        data={"statement": "Fix bugs", "label": "mh:bootstrap", "repos": ["davecthomas/meta-harness"]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/goals/g-1"
    assert goals.calls == [("create", "dave@example.com")]
    page = client.get("/goals/g-1")
    assert "Fix bugs" in page.text
    assert "github:davecthomas/meta-harness#5" in page.text
    assert "pull/9" in page.text


def test_a_refused_goal_shows_why():
    client, _ = _client()
    response = client.post("/goals", data={"statement": "x", "label": "bad", "repos": ["davecthomas/meta-harness"]})
    assert response.status_code == 422
    assert "not in the repo registry" in response.text


def test_the_overview_lists_goals_and_what_needs_the_operator():
    client, goals = _client()
    goals.create(
        actor="dave@example.com",
        statement="Fix bugs",
        label="mh:x",
        repos=["davecthomas/meta-harness"],
        auto_merge=False,
    )
    page = client.get("/")
    assert page.status_code == 200
    assert "Fix bugs" in page.text
    assert "open for you to merge" in page.text


def test_goal_actions_name_the_operator():
    client, goals = _client()
    goals.create(
        actor="dave@example.com",
        statement="Fix bugs",
        label="mh:x",
        repos=["davecthomas/meta-harness"],
        auto_merge=False,
    )
    client.post("/goals/g-1/pause", follow_redirects=False)
    client.post("/goals/g-1/auto-merge", data={"on": "on"}, follow_redirects=False)
    assert ("pause", "dave@example.com") in goals.calls
    assert ("patch", "dave@example.com", {"auto_merge": True}) in goals.calls
