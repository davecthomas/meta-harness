"""The repos an operator can reach on GitHub with their own connection (github_user.py).

    GET /v1/repos/reachable    the caller's repos, each marked writable or read-only

A goal acts with what its operator can already do on GitHub, so this list is what a goal of theirs may work
in: every repo they can push to, with no installation by an org owner and no entry in the repo registry. A
repo they can only read is listed as read-only: it can be read, and no task may change it.

The list is GitHub's own answer, read with the operator's token and held for a short time, since a picker asks
for it as the operator types. `?refresh=1` reads it again. A read that fails is retried by the HTTP client
before anything is said; when it still fails, the last good list is served with its age, and the reason.
"""

import time
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query

from meta_harness import fallback, github_user
from meta_harness.planner import github_api

SETTING = "MH_REACHABLE_REPOS_SECONDS"
DEFAULT_SECONDS = 600
# The most repos read for one operator: GitHub lists a hundred a page.
MAX_PAGES = 30
# identity -> (when read, the repos).
_held: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def hold_seconds() -> float:
    """How long a list is held before it is read again. A bad setting falls back to the default, and says so
    (fallback.py): the default is safe, since it only decides how fresh a list is."""
    return fallback.seconds(SETTING, DEFAULT_SECONDS)


def _entry(repo: dict[str, Any]) -> dict[str, Any]:
    may = repo.get("permissions") or {}
    return {
        "name": repo["full_name"],
        "description": repo.get("description") or "",
        "private": bool(repo.get("private")),
        "default_branch": repo.get("default_branch") or "",
        "language": repo.get("language") or "",
        "pushed_at": repo.get("pushed_at") or "",
        "access": "write" if may.get("push") else "read",
    }


async def read(token: str, transport: httpx.AsyncBaseTransport | None = None) -> list[dict[str, Any]]:
    """Every repo the token can see that is not archived, most recently pushed first."""
    found: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        batch = await github_user.call(
            token, "GET", "/user/repos", transport, params={"per_page": 100, "page": page, "sort": "pushed"}
        )
        found += [_entry(repo) for repo in batch or [] if not repo.get("archived")]
        if len(batch or []) < 100:
            break
    return found


async def reachable(
    identity: str, refresh: bool = False, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    """The operator's reachable repos: {repos, age_seconds, stale, why}. stale: the list could not be read
    again, so this is the last good one.

    Raises:
        github_user.GitHubUserError: The operator has no working connection, or the list could not be read and
            none is held.
    """
    held = _held.get(identity)
    if held and not refresh and time.time() - held[0] < hold_seconds():
        return {"repos": held[1], "age_seconds": int(time.time() - held[0]), "stale": False, "why": None}
    token = github_api.token_of(identity)
    if token is None:
        raise github_user.GitHubUserError("you have not connected GitHub. Connect it in Settings", reconnect=True)
    try:
        repos = await read(token, transport)
    except github_user.GitHubUserError as err:
        if err.reconnect:
            github_api.mark_broken(identity)
            _held.pop(identity, None)
            raise
        if held:
            return {"repos": held[1], "age_seconds": int(time.time() - held[0]), "stale": True, "why": str(err)}
        raise
    _held[identity] = (time.time(), repos)
    return {"repos": repos, "age_seconds": 0, "stale": False, "why": None}


def can_push(identity: str, repo: str) -> bool | None:
    """Whether the operator's held list says they can push to the repo; None when no list is held."""
    held = _held.get(identity)
    if not held:
        return None
    return any(r["name"].lower() == repo.lower() and r["access"] == "write" for r in held[1])


def register(app: FastAPI, operator: Callable[..., str], transport: httpx.AsyncBaseTransport | None = None) -> None:
    @app.get("/v1/repos/reachable")
    async def list_reachable(refresh: bool = Query(False), who: str = Depends(operator)) -> dict:
        """The caller's repos on GitHub, each `write` or `read`. 409 when they have not connected GitHub."""
        try:
            return await reachable(who, refresh, transport)
        except github_user.GitHubUserError as err:
            raise HTTPException(status_code=409 if err.reconnect else 502, detail=str(err)) from err
