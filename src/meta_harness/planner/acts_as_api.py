"""Whose GitHub access a goal uses, as the goal page shows it, and an operator taking a goal over.

A goal acts as the operator who created it once they had connected GitHub (meta_harness.acting). Another
operator can take it over ("Act as me"): from then on the goal uses their connection, so each of its repos must
be one they can push to.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException

from meta_harness import acting, github_user, local_repos
from meta_harness.planner import github_api, reach


def shown(goal: dict, who: str) -> dict[str, Any]:
    """What the goal page says of whose GitHub access the goal uses. Never a token."""
    acts_as = goal.get("acts_as") or None
    row = github_api.row_of(acts_as) if acts_as else None
    mine = github_api.row_of(who)
    return {
        "acts_as": acts_as,
        # The GitHub account its pull requests come from; None for the GitHub App.
        "login": row["login"] if row and row["login"] else None,
        # connected, broken, or none when the operator has disconnected: the goal stops where it next needs GitHub.
        "state": (row["state"] if row and row["state"] != "pending" else "none") if acts_as else None,
        "mine": bool(acts_as) and acts_as == who,
        # Whether the caller could take the goal over: they have a working connection and it is not theirs yet.
        "can_take": bool(mine and mine["state"] == "connected") and acts_as != who,
    }


def register(
    app: FastAPI,
    operator: Callable[..., str],
    load: Callable[[str], dict],
    events: Any,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    @app.get("/v1/goals/{goal_id}/acts-as")
    def acts_as(goal_id: str, who: str = Depends(operator)) -> dict:
        return shown(load(goal_id), who)

    @app.post("/v1/goals/{goal_id}/act-as-me")
    async def act_as_me(goal_id: str, who: str = Depends(operator)) -> dict:
        """The goal uses the caller's GitHub connection from now on. 409 when they have none; 422 when they
        cannot push to one of its repos."""
        goal = await asyncio.to_thread(load, goal_id)
        if not await asyncio.to_thread(acting.connected, who):
            raise HTTPException(status_code=409, detail="you have not connected GitHub. Connect it in Settings")
        try:
            found = await reach.reachable(who, transport=transport)
        except github_user.GitHubUserError as err:
            raise HTTPException(status_code=409 if err.reconnect else 502, detail=str(err)) from err
        pushable = {r["name"].lower() for r in found["repos"] if r["access"] == "write"}
        missing = [r for r in goal["repos"] if not local_repos.is_local(r) and r.lower() not in pushable]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"your GitHub connection cannot push to {', '.join(missing)}, which this goal changes. Get push "
                    "access to it, or add the account that owns it under GitHub in Settings"
                ),
            )
        was = goal.get("acts_as") or ""
        if was != who:
            await asyncio.to_thread(acting.set_for, goal_id, who)
            events.emit("goal_acts_as_changed", {"goal_id": goal_id, "acts_as": who, "was": was}, actor=who)
        return shown({**goal, "acts_as": who}, who)
