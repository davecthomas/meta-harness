"""An operator's own GitHub access, through the OAuth app they sign in to from the console.

The meta-harness reaches GitHub in two ways. As a GitHub App (github_app.py), which an org owner installs on
repos: a goal with no operator uses that. And as an operator: they connect their GitHub account once, and a
goal of theirs acts with what they can already reach, with no step by an org owner or an admin. This module is
the second: the sign-in, and calls made with an operator's token.

The OAuth app is the deployment's: its client id and secret come from the environment
(`MH_GITHUB_OAUTH_CLIENT_ID`, `MH_GITHUB_OAUTH_CLIENT_SECRET`), with the key stored tokens are encrypted with
(`MH_GITHUB_TOKEN_KEY`, a Fernet key). All three or none: with none, connecting is off and goals use the App
as before. A token is encrypted before it is stored and decrypted only to make a call. It is never logged, put
in an event, or passed into a sandbox.
"""

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from meta_harness import retrying

API = "https://api.github.com"
AUTHORIZE = "https://github.com/login/oauth/authorize"
EXCHANGE = "https://github.com/login/oauth/access_token"
# What a connection may do: read and push to the repos the operator can, and read and write their issues and
# pull requests. GitHub has no narrower scope that reaches private repos.
SCOPE = "repo"
ENV = ("MH_GITHUB_OAUTH_CLIENT_ID", "MH_GITHUB_OAUTH_CLIENT_SECRET", "MH_GITHUB_TOKEN_KEY")


class GitHubUserError(RuntimeError):
    """A call to GitHub as an operator failed. The message says what to do. `reconnect`: GitHub refused the
    token, so the operator must connect again."""

    def __init__(self, message: str, reconnect: bool = False) -> None:
        super().__init__(message)
        self.reconnect = reconnect


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    token_key: str


def settings() -> Settings | None:
    """The OAuth app and the token key, or None when the deployment has none."""
    found = [os.environ.get(name, "").strip() for name in ENV]
    return Settings(*found) if all(found) else None


def check_settings() -> None:
    """Raises RuntimeError when the connection is half configured, or its token key is not a key.

    All three settings or none: a client id with no secret signs nobody in, and tokens with no key cannot be
    stored. None at all is a deployment without the feature, which is fine."""
    found = {name: os.environ.get(name, "").strip() for name in ENV}
    missing = [name for name, value in found.items() if not value]
    if missing and len(missing) < len(ENV):
        raise RuntimeError(
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set, and "
            f"{', '.join(n for n in ENV if n not in missing)} {'are' if len(ENV) - len(missing) > 1 else 'is'}. "
            "An operator's GitHub connection needs all three: set the missing, or unset them all to turn it off"
        )
    if not missing:
        try:
            Fernet(found["MH_GITHUB_TOKEN_KEY"].encode())
        except (ValueError, TypeError) as err:
            raise RuntimeError(
                "MH_GITHUB_TOKEN_KEY is not a key tokens can be encrypted with. Make one with "
                '`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` '
                "and set it"
            ) from err


def encrypt(config: Settings, token: str) -> str:
    return Fernet(config.token_key.encode()).encrypt(token.encode()).decode()


def decrypt(config: Settings, stored: str) -> str:
    """The token as GitHub gave it.

    Raises:
        GitHubUserError: It was encrypted with another key, so it is lost: the operator connects again.
    """
    try:
        return Fernet(config.token_key.encode()).decrypt(stored.encode()).decode()
    except InvalidToken as err:
        raise GitHubUserError(
            "the stored GitHub connection cannot be read with this deployment's token key. Connect GitHub again",
            reconnect=True,
        ) from err


def authorize_url(config: Settings, nonce: str) -> str:
    """Where the operator's browser goes to sign in to GitHub and consent. GitHub sends it back to the OAuth
    app's own callback address with a code and this nonce."""
    return f"{AUTHORIZE}?{urlencode({'client_id': config.client_id, 'scope': SCOPE, 'state': nonce})}"


def _client(transport: httpx.AsyncBaseTransport | None, token: str | None = None) -> httpx.AsyncClient:
    headers = {"Accept": "application/json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0), transport=retrying.async_transport(transport), headers=headers
    )


async def exchange(config: Settings, code: str, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, str]:
    """Trades the code GitHub sent back for the operator's token: {token, scopes}.

    Raises:
        GitHubUserError: GitHub refused the code, or could not be reached after its retries.
    """
    try:
        async with _client(transport) as http:
            response = await http.post(
                EXCHANGE, data={"client_id": config.client_id, "client_secret": config.client_secret, "code": code}
            )
    except httpx.HTTPError as err:
        raise GitHubUserError(f"GitHub could not be reached to finish connecting ({err}). Connect again") from err
    body: dict[str, Any] = response.json() if response.status_code == 200 else {}
    if not body.get("access_token"):
        why = body.get("error_description") or body.get("error") or f"HTTP {response.status_code}"
        raise GitHubUserError(f"GitHub did not accept the sign-in ({why}). Connect again")
    return {"token": str(body["access_token"]), "scopes": str(body.get("scope") or "")}


async def call(
    token: str, method: str, path: str, transport: httpx.AsyncBaseTransport | None = None, **kwargs: Any
) -> Any:
    """A REST call as the operator. Returns the parsed body.

    Raises:
        GitHubUserError: The call failed. `reconnect` when GitHub refused the token.
    """
    try:
        async with _client(transport, token) as http:
            response = await http.request(method, f"{API}{path}", **kwargs)
    except httpx.HTTPError as err:
        raise GitHubUserError(f"GitHub could not be reached ({err}). Try again in a minute") from err
    if response.status_code == 401:
        raise GitHubUserError("GitHub no longer accepts this connection. Connect GitHub again", reconnect=True)
    if response.status_code >= 400:
        raise GitHubUserError(f"GitHub answered {response.status_code} to {method} {path}: {response.text[:200]}")
    return response.json() if response.content else None


async def login_of(token: str, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """The GitHub login the token is for."""
    return str((await call(token, "GET", "/user", transport))["login"])
