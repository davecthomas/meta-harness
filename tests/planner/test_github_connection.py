"""An operator's own GitHub connection: the sign-in, and what the console is told of it. No test calls GitHub."""

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from meta_harness import github_user
from meta_harness.planner import github_api
from meta_harness.planner.service import create_app

from .test_planner import OPERATOR, Events, FakeTasks, _no_title, migrated, registry  # noqa: F401
from .test_planning import WritingTracker

KEY = Fernet.generate_key().decode()


class GitHub(httpx.AsyncBaseTransport):
    """GitHub's sign-in and API, as far as a connection needs them."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []
        self.refuses_code = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.asked.append((request.method, str(request.url)))
        if str(request.url) == github_user.EXCHANGE:
            sent = dict(pair.split("=") for pair in request.content.decode().split("&"))
            assert sent["client_id"] == "cid" and sent["client_secret"] == "shh"
            if self.refuses_code or sent["code"] != "good-code":
                return httpx.Response(
                    200,
                    json={"error": "bad_verification_code", "error_description": "The code is incorrect or expired."},
                )
            return httpx.Response(200, json={"access_token": "gho_secret_token", "scope": "repo"})
        if request.url.path == "/user":
            assert request.headers["authorization"] == "Bearer gho_secret_token"
            return httpx.Response(200, json={"login": "octo-dave"})
        return httpx.Response(404, json={})


@pytest.fixture
def configured(monkeypatch):
    for name, value in zip(github_user.ENV, ("cid", "shh", KEY), strict=True):
        monkeypatch.setenv(name, value)


@pytest.fixture
def world(migrated, registry):  # noqa: F811
    from meta_harness import db

    with db.connect(autocommit=True) as conn:
        conn.execute("DELETE FROM planner.github_connection")
    events, github = Events(), GitHub()
    app = create_app(
        events,
        WritingTracker(),
        FakeTasks(),
        operator_keys={"op-key": "dave", "pat-key": "pat"},
        service_keys={},
        sweep_seconds=0,
        titler=_no_title,
        github_transport=github,
    )
    with TestClient(app) as client:
        yield client, events, github


PAT = {"Authorization": "Bearer pat-key"}


def test_an_operator_connects_github_once_and_the_token_is_stored_encrypted(world, configured):
    client, events, github = world
    assert client.get("/v1/github/connection", headers=OPERATOR).json() == {
        "enabled": True, "why_off": None, "state": "none", "connected": False, "login": None, "scopes": "", "connected_at": None, "owners": [],
    }  # fmt: skip

    started = client.post("/v1/github/connect", headers=OPERATOR)

    assert started.status_code == 200
    url = httpx.URL(started.json()["url"])
    assert str(url).startswith(github_user.AUTHORIZE) and url.params["client_id"] == "cid"
    assert url.params["scope"] == "repo" and len(url.params["state"]) >= 32
    assert "shh" not in str(url), "the app's secret never goes to the browser"

    finished = client.post(
        "/v1/github/connection", json={"code": "good-code", "state": url.params["state"]}, headers=OPERATOR
    )

    assert finished.status_code == 200, finished.text
    shown = finished.json()
    assert shown["connected"] is True and shown["login"] == "octo-dave" and shown["scopes"] == "repo"
    assert "gho_secret_token" not in finished.text, "the token is never sent to the console"
    row = github_api.row_of("dave")
    assert row["token"] != "gho_secret_token" and "gho_" not in row["token"], "it is stored encrypted"
    assert github_api.token_of("dave") == "gho_secret_token" and row["nonce"] is None
    assert ("github_connected", {"login": "octo-dave", "scopes": "repo"}) in events.events
    assert "gho_secret_token" not in str(events.events), "and never put in an event"
    # The same return used again is refused: its state was used.
    again = client.post(
        "/v1/github/connection", json={"code": "good-code", "state": url.params["state"]}, headers=OPERATOR
    )
    assert again.status_code == 409 and "Press Connect GitHub to start again" in again.json()["detail"]


def test_a_return_that_another_operator_started_is_refused(world, configured):
    """Nobody can attach their GitHub account to someone else's console identity."""
    client, _, github = world
    state = httpx.URL(client.post("/v1/github/connect", headers=OPERATOR).json()["url"]).params["state"]

    stolen = client.post("/v1/github/connection", json={"code": "good-code", "state": state}, headers=PAT)

    assert stolen.status_code == 409 and "was not started from your console session" in stolen.json()["detail"]
    assert github.asked == [], "GitHub is not asked for a token"
    assert client.get("/v1/github/connection", headers=PAT).json()["connected"] is False


def test_a_code_github_refuses_says_so_and_connects_nothing(world, configured):
    client, _, github = world
    state = httpx.URL(client.post("/v1/github/connect", headers=OPERATOR).json()["url"]).params["state"]

    refused = client.post("/v1/github/connection", json={"code": "stale", "state": state}, headers=OPERATOR)

    assert refused.status_code == 502
    assert (
        refused.json()["detail"]
        == "GitHub did not accept the sign-in (The code is incorrect or expired.). Connect again"
    )
    assert client.get("/v1/github/connection", headers=OPERATOR).json()["state"] == "none"


def test_disconnecting_forgets_the_token_and_a_broken_connection_says_to_reconnect(world, configured):
    client, events, _ = world
    state = httpx.URL(client.post("/v1/github/connect", headers=OPERATOR).json()["url"]).params["state"]
    client.post("/v1/github/connection", json={"code": "good-code", "state": state}, headers=OPERATOR)

    github_api.mark_broken("dave")
    broken = client.get("/v1/github/connection", headers=OPERATOR).json()
    assert broken["state"] == "broken" and broken["connected"] is False and broken["login"] == "octo-dave"
    assert github_api.token_of("dave") is None, "a broken connection gives no token"

    gone = client.delete("/v1/github/connection", headers=OPERATOR)
    assert gone.json()["state"] == "none" and github_api.row_of("dave") is None
    assert ("github_disconnected", {}) in events.events


def test_with_no_oauth_app_connecting_is_off_and_says_what_to_set(world, monkeypatch):
    client, *_ = world
    for name in github_user.ENV:
        monkeypatch.delenv(name, raising=False)

    shown = client.get("/v1/github/connection", headers=OPERATOR).json()

    assert shown["enabled"] is False and "An admin sets MH_GITHUB_OAUTH_CLIENT_ID" in shown["why_off"]
    refused = client.post("/v1/github/connect", headers=OPERATOR)
    assert refused.status_code == 409 and refused.json()["detail"] == github_api.OFF


def test_a_half_configured_connection_stops_the_service_and_names_what_is_missing(monkeypatch):
    for name in github_user.ENV:
        monkeypatch.delenv(name, raising=False)
    github_user.check_settings()
    monkeypatch.setenv("MH_GITHUB_OAUTH_CLIENT_ID", "cid")
    with pytest.raises(RuntimeError, match="MH_GITHUB_OAUTH_CLIENT_SECRET, MH_GITHUB_TOKEN_KEY are not set"):
        github_user.check_settings()
    monkeypatch.setenv("MH_GITHUB_OAUTH_CLIENT_SECRET", "shh")
    monkeypatch.setenv("MH_GITHUB_TOKEN_KEY", "not-a-key")
    with pytest.raises(RuntimeError, match="MH_GITHUB_TOKEN_KEY is not a key tokens can be encrypted with"):
        github_user.check_settings()
    monkeypatch.setenv("MH_GITHUB_TOKEN_KEY", KEY)
    github_user.check_settings()


def test_a_token_stored_under_another_key_is_lost_and_says_to_connect_again():
    stored = github_user.encrypt(github_user.Settings("cid", "shh", KEY), "gho_x")
    other = github_user.Settings("cid", "shh", Fernet.generate_key().decode())

    with pytest.raises(github_user.GitHubUserError, match="Connect GitHub again") as refused:
        github_user.decrypt(other, stored)
    assert refused.value.reconnect is True


class Repos(GitHub):
    """GitHub, with the repos the operator can see."""

    def __init__(self) -> None:
        super().__init__()
        self.fails = 0
        self.unauthorized = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/user/repos":
            return await super().handle_async_request(request)
        self.asked.append((request.method, str(request.url)))
        if self.unauthorized:
            return httpx.Response(401, json={"message": "Bad credentials"})
        if self.fails:
            self.fails -= 1
            return httpx.Response(404, json={"message": "nope"})
        repo = lambda name, push, **more: {  # noqa: E731
            "full_name": name, "description": f"About {name}", "private": True, "default_branch": "main",
            "language": "Python", "pushed_at": "2026-11-25T10:00:00Z", "permissions": {"pull": True, "push": push}, **more,
        }  # fmt: skip
        return httpx.Response(
            200, json=[repo("acme/api", True), repo("acme/docs", False), repo("acme/old", True, archived=True)]
        )


@pytest.fixture
def reaching(migrated, registry, configured):  # noqa: F811
    from meta_harness import db
    from meta_harness.planner import reach

    with db.connect(autocommit=True) as conn:
        conn.execute("DELETE FROM planner.github_connection")
    reach._held.clear()
    events, github = Events(), Repos()
    app = create_app(
        events, WritingTracker(), FakeTasks(), operator_keys={"op-key": "dave"}, service_keys={},
        sweep_seconds=0, titler=_no_title, github_transport=github,
    )  # fmt: skip
    with TestClient(app) as client:
        yield client, github


def _connect(client) -> None:
    state = httpx.URL(client.post("/v1/github/connect", headers=OPERATOR).json()["url"]).params["state"]
    assert (
        client.post("/v1/github/connection", json={"code": "good-code", "state": state}, headers=OPERATOR).status_code
        == 200
    )


def test_an_operators_reachable_repos_are_githubs_answer_each_writable_or_read_only(reaching):
    from meta_harness.planner import reach

    client, github = reaching
    none = client.get("/v1/repos/reachable", headers=OPERATOR)
    assert none.status_code == 409 and none.json()["detail"] == "you have not connected GitHub. Connect it in Settings"
    _connect(client)

    found = client.get("/v1/repos/reachable", headers=OPERATOR).json()

    assert [(r["name"], r["access"]) for r in found["repos"]] == [("acme/api", "write"), ("acme/docs", "read")], (
        "an archived repo is left out, and one that can only be read says so"
    )
    assert found["repos"][0]["description"] == "About acme/api" and found["stale"] is False
    assert reach.can_push("dave", "ACME/api") is True and reach.can_push("dave", "acme/docs") is False
    assert reach.can_push("pat", "acme/api") is None, "with no list held, nothing is claimed"
    # Held for a while: a picker asking as the operator types does not ask GitHub each time.
    asked = len(github.asked)
    client.get("/v1/repos/reachable", headers=OPERATOR)
    assert len(github.asked) == asked
    client.get("/v1/repos/reachable?refresh=1", headers=OPERATOR)
    assert len(github.asked) == asked + 1


def test_a_list_that_cannot_be_read_again_is_served_as_it_was_with_why(reaching):
    client, github = reaching
    _connect(client)
    client.get("/v1/repos/reachable", headers=OPERATOR)
    github.fails = 1

    again = client.get("/v1/repos/reachable?refresh=1", headers=OPERATOR).json()

    assert again["stale"] is True and "GitHub answered 404" in again["why"]
    assert [r["name"] for r in again["repos"]] == ["acme/api", "acme/docs"]


def test_a_token_github_refuses_marks_the_connection_broken_and_says_to_reconnect(reaching):
    client, github = reaching
    _connect(client)
    github.unauthorized = True

    refused = client.get("/v1/repos/reachable", headers=OPERATOR)

    assert refused.status_code == 409
    assert refused.json()["detail"] == "GitHub no longer accepts this connection. Connect GitHub again"
    assert client.get("/v1/github/connection", headers=OPERATOR).json()["state"] == "broken"


def test_an_operator_limits_muxxy_to_the_accounts_they_pick(reaching):
    from meta_harness.planner import reach

    client, github = reaching
    _connect(client)
    everything = client.get("/v1/repos/reachable", headers=OPERATOR).json()
    assert everything["owners"] == [{"name": "acme", "repos": 2}] and everything["chosen"] == []

    wrong = client.put("/v1/github/connection/owners", json={"owners": ["nobody"]}, headers=OPERATOR)
    assert wrong.status_code == 422
    assert wrong.json()["detail"] == "your GitHub connection reaches no repo of nobody. Pick from the list"
    saved = client.put("/v1/github/connection/owners", json={"owners": ["ACME"]}, headers=OPERATOR)
    assert saved.status_code == 200 and saved.json()["owners"] == ["acme"]

    from meta_harness import db

    with db.connect(autocommit=True) as conn:
        conn.execute("UPDATE planner.github_connection SET owners = '{elsewhere}'")
    limited = client.get("/v1/repos/reachable", headers=OPERATOR).json()
    assert limited["repos"] == [] and limited["chosen"] == ["elsewhere"], "only the chosen accounts' repos are offered"
    assert limited["owners"] == [{"name": "acme", "repos": 2}], "every account stays in the list to pick from"
    assert reach.can_push("dave", "acme/api") is False
    cleared = client.put("/v1/github/connection/owners", json={"owners": []}, headers=OPERATOR)
    assert cleared.json()["owners"] == [] and reach.can_push("dave", "acme/api") is True


async def test_a_long_list_of_repos_is_read_a_few_pages_at_a_time():
    from meta_harness.planner import reach

    class Many(httpx.AsyncBaseTransport):
        pages: list[int] = []

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            self.pages.append(page)
            count = 100 if page < 8 else 30 if page == 8 else 0
            return httpx.Response(
                200, json=[{"full_name": f"acme/r{page}-{i}", "permissions": {"push": True}} for i in range(count)]
            )

    found = await reach.read("gho_x", Many())

    assert len(found) == 730 and len({r["name"] for r in found}) == 730
    assert found[0]["name"] == "acme/r1-0" and found[-1]["name"] == "acme/r8-29", "in GitHub's order"
    assert sorted(Many.pages) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]


def test_the_accounts_can_be_read_without_the_repos(reaching):
    client, _ = reaching
    _connect(client)

    found = client.get("/v1/repos/reachable?repos=0", headers=OPERATOR).json()

    assert found["repos"] == [] and found["owners"] == [{"name": "acme", "repos": 2}]


async def test_settings_lists_the_accounts_at_once_from_the_last_read(reaching):
    import asyncio

    from meta_harness.planner import github_api, reach

    client, github = reaching
    _connect(client)
    first = client.get("/v1/repos/reachable?repos=0", headers=OPERATOR).json()
    assert first["owners"] == [{"name": "acme", "repos": 2}], "the first time, the read is waited for"
    assert github_api.reached_of("dave") == [{"name": "acme", "repos": 2}]
    # The held list has gone (a restart, or ten minutes): the accounts are answered from what was kept, and the
    # repos are read again behind the answer.
    reach._held.clear()
    asked = len(github.asked)

    kept = await reach.accounts("dave", github)

    assert kept["owners"] == [{"name": "acme", "repos": 2}] and kept["repos"] == [] and kept["age_seconds"] is None
    await asyncio.gather(*reach._tasks)
    assert len(github.asked) == asked + 1 and "dave" in reach._held and not reach._reading


async def test_a_picker_is_answered_from_a_list_past_its_time_while_it_is_read_again(reaching):
    import asyncio
    import time

    from meta_harness.planner import reach

    client, github = reaching
    _connect(client)
    client.get("/v1/repos/reachable", headers=OPERATOR)
    held_at, repos = reach._held["dave"]
    # An hour old: past the ten minutes a list is held, well inside the day it may be answered from.
    reach._held["dave"] = (held_at - 3600, repos)
    asked = len(github.asked)

    found = await reach.offered("dave", github)

    assert [r["name"] for r in found["repos"]] == ["acme/api", "acme/docs"], "answered at once, from the list held"
    await asyncio.gather(*reach._tasks)
    assert len(github.asked) == asked + 1 and time.time() - reach._held["dave"][0] < 60, "and read again behind it"
    # A list older than a day is not trusted: the read is waited for.
    reach._held["dave"] = (time.time() - 2 * 86400, repos)
    asked = len(github.asked)
    await reach.offered("dave", github)
    assert len(github.asked) == asked + 1


async def test_after_a_restart_the_picker_answers_from_the_list_the_last_read_kept(reaching):
    import asyncio

    from meta_harness.planner import reach

    client, github = reaching
    _connect(client)
    client.get("/v1/repos/reachable", headers=OPERATOR)
    # A restart of the planner: nothing is held in memory.
    reach._held.clear()
    asked = len(github.asked)

    found = await reach.offered("dave", github)

    assert [r["name"] for r in found["repos"]] == ["acme/api", "acme/docs"], "answered from what the last read kept"
    assert len(github.asked) == asked, "with no wait on GitHub"
    await asyncio.gather(*reach._tasks)


def test_a_new_goal_default_repo_may_be_one_the_operator_can_push_to(reaching):
    client, _ = reaching
    mine = client.get("/v1/me/settings", headers=OPERATOR).json()
    refused = client.put(
        "/v1/me/settings", json={**mine, "planner": {**mine["planner"], "repos": ["acme/api"]}}, headers=OPERATOR
    )
    assert refused.status_code == 422, "with no connection, only the registry's repos"
    _connect(client)
    saved = client.put(
        "/v1/me/settings", json={**mine, "planner": {**mine["planner"], "repos": ["ACME/api"]}}, headers=OPERATOR
    )
    assert saved.status_code == 200 and saved.json()["planner"]["repos"] == ["acme/api"]
