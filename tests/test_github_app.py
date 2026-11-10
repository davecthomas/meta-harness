import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from meta_harness.github_app import AppConfig, GitHubAppError, GitHubApps, git_auth_env, parse_apps


@pytest.fixture
def key_file(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "app.pem"
    path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return path


class FakeGitHub:
    def __init__(self) -> None:
        self.minted: list[dict] = []
        self.installation_lookups = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/octo/repo/installation":
            self.installation_lookups += 1
            return httpx.Response(200, json={"id": 42})
        if request.url.path == "/app/installations/42/access_tokens":
            body = json.loads(request.content)
            self.minted.append(body)
            return httpx.Response(201, json={"token": f"tok-{len(self.minted)}", "expires_at": "2099-01-01T00:00:00Z"})
        if request.url.path == "/repos/octo/repo/issues/7":
            assert request.headers["authorization"].startswith("token tok-")
            return httpx.Response(200, json={"state": "open", "title": "t", "body": "b"})
        return httpx.Response(404, json={"message": "Not Found"})


def test_parse_apps_reads_owner_app_and_key():
    apps = parse_apps("octo=123:~/k.pem, Other=9:/abs/k.pem")
    assert apps["octo"].app_id == "123"
    assert str(apps["other"].key_file) == "/abs/k.pem"
    with pytest.raises(ValueError):
        parse_apps("octo=abc:/k.pem")


async def test_a_token_is_scoped_to_one_repo_and_cached(key_file):
    github = FakeGitHub()
    apps = GitHubApps({"octo": AppConfig("octo", "1", key_file)}, transport=httpx.MockTransport(github.handler))
    first = await apps.token("octo/repo", {"contents": "read"})
    again = await apps.token("octo/repo", {"contents": "read"})
    write = await apps.token("octo/repo", {"contents": "write"})
    assert first == again != write
    assert github.minted == [
        {"repositories": ["repo"], "permissions": {"contents": "read"}},
        {"repositories": ["repo"], "permissions": {"contents": "write"}},
    ]
    assert github.installation_lookups == 1


async def test_a_call_uses_the_installation_token(key_file):
    github = FakeGitHub()
    apps = GitHubApps({"octo": AppConfig("octo", "1", key_file)}, transport=httpx.MockTransport(github.handler))
    issue = await apps.call("octo/repo", {"issues": "read"}, "GET", "/repos/octo/repo/issues/7")
    assert issue["state"] == "open"


async def test_an_owner_without_an_app_is_refused(key_file):
    apps = GitHubApps({"octo": AppConfig("octo", "1", key_file)})
    with pytest.raises(GitHubAppError) as err:
        await apps.token("someone/repo", {"contents": "read"})
    assert err.value.retryable is False


def test_git_auth_env_carries_the_token_only_in_the_environment():
    env = git_auth_env("secret-token")
    header = env["GIT_CONFIG_VALUE_0"].removeprefix("AUTHORIZATION: basic ")
    assert base64.b64decode(header).decode() == "x-access-token:secret-token"
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"


async def test_installed_lists_every_repo_each_app_is_installed_on_with_its_install_page(key_file):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/app":
            return httpx.Response(200, json={"html_url": "https://github.com/apps/mh"})
        if path == "/app/installations":
            return httpx.Response(200, json=[{"id": 42}])
        if path == "/app/installations/42/access_tokens":
            assert json.loads(request.content) == {"permissions": {"metadata": "read"}}
            return httpx.Response(201, json={"token": "tok", "expires_at": "2099-01-01T00:00:00Z"})
        if path == "/installation/repositories":
            assert request.headers["authorization"] == "token tok"
            return httpx.Response(200, json={"repositories": [{"full_name": "octo/repo", "private": True}]})
        return httpx.Response(404, json={"message": "Not Found"})

    apps = GitHubApps({"octo": AppConfig("octo", "1", key_file)}, transport=httpx.MockTransport(handler))

    assert await apps.installed() == [
        {"name": "octo/repo", "private": True, "install_url": "https://github.com/apps/mh/installations/new"}
    ]
