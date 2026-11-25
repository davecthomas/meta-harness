"""A goal acts as its operator on GitHub: whose access the code host uses. No test calls GitHub."""

import httpx
import pytest

from meta_harness import acting
from meta_harness.planner import github_api
from meta_harness.run_service import code_host
from meta_harness.run_service.shared import PublishRequest

from .test_github_connection import _connect, configured, reaching  # noqa: F401
from .test_planner import OPERATOR, migrated, registry  # noqa: F401


def _new_goal(client, headers) -> dict:
    made = client.post(
        "/v1/goals",
        json={"statement": "Fix bugs", "repos": ["davecthomas/meta-harness"], "label": "x"},
        headers=headers,
    )
    assert made.status_code == 201, made.text
    return made.json()


@pytest.mark.db
def test_a_goal_acts_as_the_operator_who_made_it_once_they_have_connected_github(reaching):  # noqa: F811
    client, _ = reaching
    before = _new_goal(client, OPERATOR)
    assert before["acts_as"] is None, "with no connection, the goal uses the GitHub App as before"
    _connect(client)

    goal = _new_goal(client, OPERATOR)

    assert goal["acts_as"] == "dave"
    assert client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["acts_as"] == "dave"
    acting._held.clear()
    assert acting.of_goal(goal["id"]) == "dave" and acting.of_goal(before["id"]) == "" and acting.of_goal(None) == ""
    # An activity's request names its goal, or its task: the worker reads the operator from either.
    request = PublishRequest("r", "acme/api", "T-1", "t", "c", None, "0", goal_id=goal["id"])
    assert acting.of_request(request) == "dave" and acting.of_request("a-run-id") == ""
    assert acting.token("dave") == "gho_secret_token"
    with pytest.raises(
        acting.ActingError, match="pat has no working GitHub connection.*Connect GitHub again in Settings"
    ):
        acting.token("pat")


class Api(httpx.AsyncBaseTransport):
    def __init__(self, status: int = 200) -> None:
        self.status, self.seen = status, []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append((request.method, request.url.path, request.headers["authorization"]))
        return httpx.Response(self.status, json={"number": 7} if self.status == 200 else {"message": "Bad credentials"})


async def test_a_call_as_the_operator_sends_their_token_and_a_refused_one_says_to_reconnect(monkeypatch):
    broken = []
    monkeypatch.setattr(github_api, "token_of", lambda who: "gho_theirs")
    monkeypatch.setattr(github_api, "mark_broken", broken.append)
    api = Api()

    assert await acting.call("dave", "GET", "/repos/acme/api/pulls/7", api) == {"number": 7}

    assert api.seen == [("GET", "/repos/acme/api/pulls/7", "token gho_theirs")]
    with pytest.raises(acting.ActingError, match="no longer accepts dave's connection.*Connect GitHub again") as err:
        await acting.call("dave", "GET", "/repos/acme/api/pulls/7", Api(401))
    assert broken == ["dave"] and err.value.retryable is False


async def test_the_code_host_uses_the_acting_operator_and_the_app_for_everyone_else(monkeypatch):
    calls = []

    async def as_operator(who, method, path, **kwargs):
        calls.append((who, method, path))
        return {"ok": True}

    class NoApps:
        def app_for(self, repo):
            raise code_host.GitHubAppError("no GitHub App is set up for acme")

        async def call(self, repo, permissions, method, path, **kwargs):
            calls.append(("the app", method, path))
            return {"app": True}

        async def token(self, repo, permissions):
            return "ghs_app"

    monkeypatch.setattr(acting, "call", as_operator)
    monkeypatch.setattr(acting, "token", lambda who: f"gho_{who}")
    monkeypatch.setattr(code_host, "apps", lambda: NoApps())

    assert await code_host._github("acme/api", code_host.READ_CODE, "GET", "/x") == {"app": True}
    assert await code_host._token("acme/api", code_host.READ_CODE) == "ghs_app"
    with pytest.raises(code_host.CodeHostError):
        code_host.check_repo("acme/api")
    with acting.as_operator("dave"):
        assert await code_host._github("acme/api", code_host.READ_CODE, "GET", "/x") == {"ok": True}
        assert await code_host._token("acme/api", code_host.WRITE_CODE) == "gho_dave"
        code_host.check_repo("acme/api")  # the operator's access decides: the repo needs no App
    assert acting.current() == "" and calls == [("the app", "GET", "/x"), ("dave", "GET", "/x")]


@pytest.mark.db
def test_a_goal_names_any_repo_its_operator_can_push_to_with_no_registry_entry(reaching):  # noqa: F811
    client, _ = reaching

    refused = client.post(
        "/v1/goals", json={"statement": "Fix bugs", "repos": ["acme/api"], "label": "x"}, headers=OPERATOR
    )
    assert refused.status_code == 422
    assert refused.json()["detail"] == (
        "not in the repo registry: acme/api. Connect GitHub in Settings to work in any repo you can push to"
    )
    _connect(client)
    offered = client.get("/v1/repos?pushable=1", headers=OPERATOR).json()["repos"]
    assert {"name": "acme/api", "tracker": "github", "registered": False} in offered
    assert all(r["name"] != "acme/docs" for r in offered), "a repo the operator can only read is not offered"
    assert all(r["registered"] for r in client.get("/v1/repos", headers=OPERATOR).json()["repos"])

    goal = client.post(
        "/v1/goals", json={"statement": "Fix bugs", "repos": ["ACME/api"], "label": "x"}, headers=OPERATOR
    )
    assert goal.status_code == 201, goal.text
    assert goal.json()["repos"] == ["acme/api"] and goal.json()["acts_as"] == "dave"
    read_only = client.patch(f"/v1/goals/{goal.json()['id']}", json={"repos": ["acme/docs"]}, headers=OPERATOR)
    assert read_only.status_code == 422
    assert read_only.json()["detail"].startswith("your GitHub connection cannot push to acme/docs. Pick a repo")


async def test_a_tracker_or_spec_read_uses_the_acting_operator_and_the_app_otherwise(monkeypatch):
    from meta_harness import github_app

    seen = []

    class Apps:
        async def call(self, repo, permissions, method, path, **kwargs):
            seen.append(("the app", path))
            return []

    async def as_operator(who, method, path, **kwargs):
        seen.append((who, path))
        if path.endswith("/broken"):
            raise acting.ActingError("GitHub answered 404", status=404)
        return []

    monkeypatch.setattr(acting, "call", as_operator)
    await acting.github(Apps(), "acme/api", {}, "GET", "/repos/acme/api/issues")
    with acting.as_operator("dave"):
        await acting.github(Apps(), "acme/api", {}, "GET", "/repos/acme/api/issues")
        with pytest.raises(github_app.GitHubAppError) as err:
            await acting.github(Apps(), "acme/api", {}, "GET", "/repos/acme/api/broken")
    assert err.value.status == 404
    assert seen == [
        ("the app", "/repos/acme/api/issues"),
        ("dave", "/repos/acme/api/issues"),
        ("dave", "/repos/acme/api/broken"),
    ]


@pytest.mark.db
def test_the_goal_page_says_whose_access_a_goal_uses_and_another_operator_takes_it_over(migrated, registry, configured):  # noqa: F811
    from fastapi.testclient import TestClient

    from meta_harness import db
    from meta_harness.planner import reach
    from meta_harness.planner.service import create_app

    from .test_github_connection import PAT, Repos
    from .test_planner import Events, FakeTasks, _no_title
    from .test_planning import WritingTracker

    with db.connect(autocommit=True) as conn:
        conn.execute("DELETE FROM planner.github_connection")
    reach._held.clear()
    events = Events()
    app = create_app(
        events, WritingTracker(), FakeTasks(), operator_keys={"op-key": "dave", "pat-key": "pat"}, service_keys={},
        sweep_seconds=0, titler=_no_title, github_transport=Repos(),
    )  # fmt: skip
    with TestClient(app) as client:
        goal = _new_goal(client, OPERATOR)
        at = f"/v1/goals/{goal['id']}"
        assert client.get(f"{at}/acts-as", headers=OPERATOR).json() == {
            "acts_as": None, "login": None, "state": None, "mine": False, "can_take": False,
        }  # fmt: skip
        none = client.post(f"{at}/act-as-me", headers=PAT)
        assert (
            none.status_code == 409 and none.json()["detail"] == "you have not connected GitHub. Connect it in Settings"
        )

        state = httpx.URL(client.post("/v1/github/connect", headers=PAT).json()["url"]).params["state"]
        assert (
            client.post("/v1/github/connection", json={"code": "good-code", "state": state}, headers=PAT).status_code
            == 200
        )
        assert client.get(f"{at}/acts-as", headers=PAT).json()["can_take"] is True
        # The goal changes a repo pat's connection cannot push to: taking it over is refused, with what to do.
        refused = client.post(f"{at}/act-as-me", headers=PAT)
        assert refused.status_code == 422
        assert refused.json()["detail"].startswith("your GitHub connection cannot push to davecthomas/meta-harness")

        with db.connect(autocommit=True) as conn:
            conn.execute("UPDATE planner.goal SET repos = '{acme/api}' WHERE id = %s", (goal["id"],))
        taken = client.post(f"{at}/act-as-me", headers=PAT)

        assert taken.status_code == 200 and taken.json() == {
            "acts_as": "pat", "login": "octo-dave", "state": "connected", "mine": True, "can_take": False,
        }  # fmt: skip
        assert acting.of_goal(goal["id"]) == "pat"
        changed = [e for e in events.events if e[0] == "goal_acts_as_changed"]
        assert [e[1] for e in changed] == [{"goal_id": goal["id"], "acts_as": "pat", "was": ""}]
        # To the operator it is not theirs, and their own connection is missing: nothing to take with.
        assert client.get(f"{at}/acts-as", headers=OPERATOR).json()["mine"] is False
