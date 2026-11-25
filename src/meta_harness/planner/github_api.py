"""An operator's GitHub connection: connect, see it, and disconnect.

    GET    /v1/github/connection   the caller's connection: whether connecting is set up, and theirs if they have one
    POST   /v1/github/connect      start a sign-in: where to send the browser
    POST   /v1/github/connection   finish it, with the code and state GitHub sent back
    DELETE /v1/github/connection   disconnect

An operator connects once. GitHub sends their browser back to the console with a code, which the console posts
here with the session it already has, so the connection belongs to whoever is signed in to the console and to
nobody else. The state is a one-time value this service made for that operator when the sign-in began: a
return that does not carry it is refused, so nobody can attach their GitHub account to another operator.

Each connection is one row of `planner.github_connection`, with the token encrypted (github_user.py).
"""

import logging
import secrets
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from meta_harness import db, github_user

log = logging.getLogger("meta_harness.planner")

OFF = (
    "connecting GitHub is not set up on this deployment. An admin sets MH_GITHUB_OAUTH_CLIENT_ID, "
    "MH_GITHUB_OAUTH_CLIENT_SECRET and MH_GITHUB_TOKEN_KEY, then deploys"
)


class FinishIn(BaseModel):
    code: str = Field(min_length=1, max_length=400)
    state: str = Field(min_length=1, max_length=200)


class OwnersIn(BaseModel):
    owners: list[str]


def row_of(identity: str) -> dict | None:
    with db.connect(autocommit=True) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT identity, login, token, scopes, state, nonce, connected_at, checked_at, owners "
            "FROM planner.github_connection WHERE identity = %s",
            (identity,),
        )
        return cur.fetchone()


def token_of(identity: str) -> str | None:
    """The operator's GitHub token, for a call made as them; None when they have no working connection.

    Raises:
        github_user.GitHubUserError: The stored token cannot be read with this deployment's key.
    """
    config = github_user.settings()
    row = row_of(identity) if config else None
    if not row or row["state"] != "connected" or not row["token"]:
        return None
    return github_user.decrypt(config, row["token"])


def mark_broken(identity: str) -> None:
    """GitHub refused the operator's token: the connection says so until they connect again."""
    with db.connect(autocommit=True) as conn:
        conn.execute(
            "UPDATE planner.github_connection SET state = 'broken', checked_at = now(), updated_at = now() "
            "WHERE identity = %s",
            (identity,),
        )


def shown(identity: str) -> dict[str, Any]:
    """The operator's connection as the console shows it. Never the token."""
    enabled = github_user.settings() is not None
    row = row_of(identity) if enabled else None
    connected = bool(row and row["state"] == "connected")
    return {
        "enabled": enabled,
        "why_off": None if enabled else OFF,
        "state": row["state"] if row and row["state"] != "pending" else "none",
        "connected": connected,
        "login": row["login"] if row and row["login"] else None,
        "scopes": row["scopes"] if connected else "",
        "connected_at": row["connected_at"].isoformat() if row and row["connected_at"] else None,
        # The accounts and organizations the operator chose to use. Empty: every one they can reach.
        "owners": list(row["owners"]) if connected else [],
    }


def owners_of(identity: str) -> list[str]:
    """The accounts and organizations the operator limits Muxxy to; empty for all they can reach."""
    row = row_of(identity)
    return list(row["owners"]) if row else []


def register(
    app: FastAPI,
    operator: Callable[..., str],
    events: Any,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Adds the routes. transport: a test's stand-in for GitHub."""

    def config_or_409() -> github_user.Settings:
        config = github_user.settings()
        if config is None:
            raise HTTPException(status_code=409, detail=OFF)
        return config

    @app.get("/v1/github/connection")
    def connection(who: str = Depends(operator)) -> dict:
        return shown(who)

    @app.post("/v1/github/connect")
    def connect(who: str = Depends(operator)) -> dict:
        """Start a sign-in for the caller. Returns where to send their browser. A sign-in started again replaces
        the one before it, and leaves a working connection in place until the new one finishes."""
        config = config_or_409()
        nonce = secrets.token_urlsafe(32)
        with db.connect(autocommit=True) as conn:
            conn.execute(
                "INSERT INTO planner.github_connection (identity, nonce) VALUES (%s, %s) "
                "ON CONFLICT (identity) DO UPDATE SET nonce = EXCLUDED.nonce, updated_at = now()",
                (who, nonce),
            )
        return {"url": github_user.authorize_url(config, nonce)}

    @app.post("/v1/github/connection")
    async def finish(body: FinishIn, who: str = Depends(operator)) -> dict:
        """Finish the caller's sign-in with what GitHub sent back."""
        config = config_or_409()
        row = row_of(who)
        if not row or not row["nonce"] or not secrets.compare_digest(str(row["nonce"]), body.state):
            raise HTTPException(
                status_code=409,
                detail="this sign-in was not started from your console session, or it was already used. "
                "Press Connect GitHub to start again",
            )
        try:
            got = await github_user.exchange(config, body.code, transport)
            login = await github_user.login_of(got["token"], transport)
        except github_user.GitHubUserError as err:
            raise HTTPException(status_code=502, detail=str(err)) from err
        with db.connect(autocommit=True) as conn:
            conn.execute(
                "UPDATE planner.github_connection SET login = %s, token = %s, scopes = %s, state = 'connected', "
                "nonce = NULL, connected_at = now(), checked_at = now(), updated_at = now() WHERE identity = %s",
                (login, github_user.encrypt(config, got["token"]), got["scopes"], who),
            )
        events.emit("github_connected", {"login": login, "scopes": got["scopes"]}, actor=who)
        log.info("%s connected GitHub as %s", who, login)
        return shown(who)

    @app.put("/v1/github/connection/owners")
    async def choose_owners(body: OwnersIn, who: str = Depends(operator)) -> dict:
        """Limit the caller's connection to these accounts and organizations; none for all they can reach.
        422 for a name their connection cannot reach."""
        from meta_harness.planner import reach

        row = row_of(who)
        if not row or row["state"] != "connected":
            raise HTTPException(status_code=409, detail="you have not connected GitHub. Connect it in Settings")
        try:
            known = {o["name"].lower(): o["name"] for o in (await reach.reachable(who, transport=transport))["owners"]}
        except github_user.GitHubUserError as err:
            raise HTTPException(status_code=409 if err.reconnect else 502, detail=str(err)) from err
        unknown = sorted(name for name in body.owners if name.lower() not in known)
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"your GitHub connection reaches no repo of {', '.join(unknown)}. Pick from the list",
            )
        chosen = sorted({known[name.lower()] for name in body.owners})
        with db.connect(autocommit=True) as conn:
            conn.execute(
                "UPDATE planner.github_connection SET owners = %s, updated_at = now() WHERE identity = %s",
                (chosen, who),
            )
        return shown(who)

    @app.delete("/v1/github/connection")
    def disconnect(who: str = Depends(operator)) -> dict:
        """Forget the caller's connection. Their goals then stop where they next need GitHub as them."""
        with db.connect(autocommit=True) as conn:
            gone = conn.execute("DELETE FROM planner.github_connection WHERE identity = %s", (who,))
        if gone.rowcount:
            events.emit("github_disconnected", {}, actor=who)
        return shown(who)
