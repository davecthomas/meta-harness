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
