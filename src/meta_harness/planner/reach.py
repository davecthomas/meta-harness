"""The repos an operator can reach on GitHub with their own connection (github_user.py).

    GET /v1/repos/reachable    the caller's repos, each marked writable or read-only

A goal acts with what its operator can already do on GitHub, so this list is what a goal of theirs may work
in: every repo they can push to, with no installation by an org owner and no entry in the repo registry. A
repo they can only read is listed as read-only: it can be read, and no task may change it.

The list is GitHub's own answer, read with the operator's token and held for a short time, since a picker asks
for it as the operator types. `?refresh=1` reads it again. A read that fails is retried by the HTTP client
before anything is said; when it still fails, the last good list is served with its age, and the reason.
"""

import asyncio
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
PAGES_AT_ONCE = 6
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

    async def page(number: int) -> list[dict[str, Any]]:
        params = {"per_page": 100, "page": number, "sort": "pushed"}
        return await github_user.call(token, "GET", "/user/repos", transport, params=params) or []

    # Pages are read a few at a time: an operator with hundreds of repos otherwise waits on each in turn.
    batches = [await page(1)]
    first = 2
    while len(batches[-1]) == 100 and first <= MAX_PAGES:
        last = min(first + PAGES_AT_ONCE, MAX_PAGES + 1)
        got = await asyncio.gather(*(page(n) for n in range(first, last)))
        # Pages past the end come back empty, so the last one kept is the first short one.
        short = next((i for i, batch in enumerate(got) if len(batch) < 100), len(got) - 1)
        batches += got[: short + 1]
        first = last
    return [_entry(repo) for batch in batches for repo in batch if not repo.get("archived")]


async def reachable(
    identity: str, refresh: bool = False, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    """The operator's reachable repos: {repos, owners, chosen, age_seconds, stale, why}. repos: those of the
    accounts and organizations they chose, or all. owners: every one their connection reaches. stale: the list could not be read
    again, so this is the last good one.

    Raises:
        github_user.GitHubUserError: The operator has no working connection, or the list could not be read and
            none is held.
    """
    held = _held.get(identity)
    if held and not refresh and time.time() - held[0] < hold_seconds():
        return _answer(identity, held, False, None)
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
            return _answer(identity, held, True, str(err))
        raise
    _held[identity] = (time.time(), repos)
    return _answer(identity, _held[identity], False, None)


def _owner(repo: dict[str, Any]) -> str:
    return str(repo["name"]).split("/")[0]


def _chosen(identity: str, repos: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """The owners the operator limits Muxxy to, and the repos of those; every repo when they chose none."""
    chosen = github_api.owners_of(identity)
    if not chosen:
        return [], repos
    wanted = {name.lower() for name in chosen}
    return chosen, [repo for repo in repos if _owner(repo).lower() in wanted]


def _answer(identity: str, held: tuple[float, list[dict[str, Any]]], stale: bool, why: str | None) -> dict[str, Any]:
    """What the console is told: the repos of the owners the operator chose, every owner their connection
    reaches with its count, and which they chose."""
    counts: dict[str, int] = {}
    for repo in held[1]:
        counts[_owner(repo)] = counts.get(_owner(repo), 0) + 1
    chosen, repos = _chosen(identity, held[1])
    return {
        "repos": repos,
        "owners": [{"name": name, "repos": counts[name]} for name in sorted(counts, key=str.lower)],
        "chosen": chosen,
        "age_seconds": int(time.time() - held[0]),
        "stale": stale,
        "why": why,
    }


def can_push(identity: str, repo: str) -> bool | None:
    """Whether the operator's held list says they can push to the repo; None when no list is held."""
    held = _held.get(identity)
    if not held:
        return None
    return any(r["name"].lower() == repo.lower() and r["access"] == "write" for r in _chosen(identity, held[1])[1])


def register(app: FastAPI, operator: Callable[..., str], transport: httpx.AsyncBaseTransport | None = None) -> None:
    @app.get("/v1/repos/reachable")
    async def list_reachable(
        refresh: bool = Query(False), with_repos: bool = Query(True, alias="repos"), who: str = Depends(operator)
    ) -> dict:
        """The caller's repos on GitHub, each `write` or `read`. repos=0 leaves the repos out and keeps the
        accounts: an operator who can reach hundreds of repos waits for the whole list otherwise. 409 when they
        have not connected GitHub."""
        try:
            found = await reachable(who, refresh, transport)
            return found if with_repos else {**found, "repos": []}
        except github_user.GitHubUserError as err:
            raise HTTPException(status_code=409 if err.reconnect else 502, detail=str(err)) from err
