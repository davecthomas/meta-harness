"""GitHub App authentication for every code-host call the control plane makes.

Each repo owner has one GitHub App, which an operator creates and installs on
that owner's repos. MH_GITHUB_APPS lists them as `owner=app_id:key_file`
pairs separated by commas, for example

    MH_GITHUB_APPS=davecthomas=5659485:~/.config/meta-harness/github-app.pem

A call asks for an installation token scoped to one repo and the permissions
it needs: a run's clone gets `contents: read` only, and a push gets
`contents: write`. Tokens are cached until five minutes before they expire.
No token is ever written to disk.
"""

import asyncio
import base64
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import jwt

API = "https://api.github.com"
# Refresh a cached token this long before GitHub expires it.
TOKEN_MARGIN_SECONDS = 300


class GitHubAppError(RuntimeError):
    """A GitHub App call failed. `retryable` is False when repeating it would fail the same way."""

    def __init__(self, message: str, retryable: bool = True, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


@dataclass(frozen=True)
class AppConfig:
    owner: str
    app_id: str
    key_file: Path


def parse_apps(value: str) -> dict[str, AppConfig]:
    """`owner=app_id:key_file,...` to {owner: AppConfig}. A malformed entry raises ValueError."""
    apps: dict[str, AppConfig] = {}
    for entry in filter(None, (part.strip() for part in value.split(","))):
        owner, sep, rest = entry.partition("=")
        app_id, sep2, key_file = rest.partition(":")
        if not (sep and sep2 and owner and app_id.isdigit() and key_file):
            raise ValueError(f"a MH_GITHUB_APPS entry is owner=app_id:key_file, not {entry!r}")
        apps[owner.lower()] = AppConfig(owner, app_id, Path(key_file).expanduser())
    return apps


def apps_from_env() -> dict[str, AppConfig]:
    return parse_apps(os.environ.get("MH_GITHUB_APPS", ""))


class GitHubApps:
    """Installation tokens and REST calls for the repos the configured apps are installed on."""

    def __init__(self, apps: dict[str, AppConfig] | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.apps = apps if apps is not None else apps_from_env()
        self._transport = transport
        self._installations: dict[str, int] = {}
        self._tokens: dict[tuple, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    def app_for(self, repo: str) -> AppConfig:
        owner = repo.split("/", 1)[0].lower()
        app = self.apps.get(owner)
        if app is None:
            raise GitHubAppError(
                f"no GitHub App for {owner}: add {owner}=<app id>:<key file> to MH_GITHUB_APPS", retryable=False
            )
        return app

    def _jwt(self, app: AppConfig) -> str:
        try:
            key = app.key_file.read_text()
        except OSError as err:
            raise GitHubAppError(f"cannot read the GitHub App key {app.key_file}: {err}", retryable=False) from err
        now = int(time.time())
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": app.app_id}, key, algorithm="RS256")

    def _client(self, auth: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=API,
            timeout=30,
            transport=self._transport,
            headers={
                "Authorization": auth,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def token(self, repo: str, permissions: dict[str, str]) -> str:
        """An installation token for one repo with exactly these permissions."""
        app = self.app_for(repo)
        key = (repo.lower(), tuple(sorted(permissions.items())))
        async with self._lock:
            cached = self._tokens.get(key)
            if cached and cached[1] - TOKEN_MARGIN_SECONDS > time.time():
                return cached[0]
            async with self._client(f"Bearer {self._jwt(app)}") as http:
                installation = self._installations.get(repo.lower())
                if installation is None:
                    found = await _call(http, "GET", f"/repos/{repo}/installation")
                    installation = int(found["id"])
                    self._installations[repo.lower()] = installation
                name = repo.split("/", 1)[1]
                minted = await _call(
                    http,
                    "POST",
                    f"/app/installations/{installation}/access_tokens",
                    json={"repositories": [name], "permissions": permissions},
                )
            expires = datetime.fromisoformat(minted["expires_at"].replace("Z", "+00:00")).timestamp()
            self._tokens[key] = (minted["token"], expires)
            return minted["token"]

    async def installed(self) -> list[dict[str, Any]]:
        """Every repo each configured app is installed on, with the page that installs the app on more.

        Each entry is {name, private, install_url}. An app that cannot be read is skipped, so one
        broken key does not hide the other owners' repos.
        """
        found: list[dict[str, Any]] = []
        for app in self.apps.values():
            try:
                async with self._client(f"Bearer {self._jwt(app)}") as http:
                    install_url = ((await _call(http, "GET", "/app")) or {}).get("html_url", "") + "/installations/new"
                    installations = await _call(http, "GET", "/app/installations", params={"per_page": 100})
                    for installation in installations or []:
                        minted = await _call(
                            http,
                            "POST",
                            f"/app/installations/{installation['id']}/access_tokens",
                            json={"permissions": {"metadata": "read"}},
                        )
                        async with self._client(f"token {minted['token']}") as repos_http:
                            page = 1
                            while True:
                                listed = await _call(
                                    repos_http,
                                    "GET",
                                    "/installation/repositories",
                                    params={"per_page": 100, "page": page},
                                )
                                batch = listed.get("repositories", [])
                                found += [
                                    {
                                        "name": r["full_name"],
                                        "private": r.get("private", False),
                                        "install_url": install_url,
                                    }
                                    for r in batch
                                ]
                                if len(batch) < 100:
                                    break
                                page += 1
            except GitHubAppError:
                continue
        return found

    async def call(self, repo: str, permissions: dict[str, str], method: str, path: str, **kwargs: Any) -> Any:
        """A REST call on the repo's installation token."""
        token = await self.token(repo, permissions)
        async with self._client(f"token {token}") as http:
            return await _call(http, method, path, **kwargs)


async def _call(http: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> Any:
    try:
        response = await http.request(method, path, **kwargs)
    except httpx.HTTPError as err:
        raise GitHubAppError(f"GitHub {method} {path}: {err}") from err
    if response.status_code >= 400:
        rate_limited = response.status_code in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0"
        raise GitHubAppError(
            f"GitHub {method} {path} answered {response.status_code}: {response.text[:300]}",
            retryable=response.status_code >= 500 or rate_limited,
            status=response.status_code,
        )
    return response.json() if response.content else None


def git_auth_env(token: str) -> dict[str, str]:
    """Environment that makes one git command send the token to github.com, without storing it anywhere."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
        "GIT_TERMINAL_PROMPT": "0",
    }
