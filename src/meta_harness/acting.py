"""Whose GitHub access a piece of work uses: the goal's operator's own connection, or the GitHub App.

A goal an operator creates after connecting GitHub acts as them (`planner.goal.acts_as`). Whatever works for
that goal names the operator here before it calls the code host, and the code host then uses the operator's
token in place of an App installation's. Nothing named: the App, as before.

The token is read from the planner's store each time it is needed, never kept here, never logged and never put
in an event.
"""

from __future__ import annotations

import contextlib
import contextvars
import time
from collections.abc import Iterator
from typing import Any

import httpx

from meta_harness import db, github_app, github_user, retrying

_acting: contextvars.ContextVar[str] = contextvars.ContextVar("mh_acting_as", default="")
# How long a goal's operator is remembered, so a loop over many tasks does not ask the database for each.
HELD_SECONDS = 30.0
_held: dict[str, tuple[float, str]] = {}


class ActingError(RuntimeError):
    """GitHub could not be used as the operator. The message says what to do."""

    def __init__(self, message: str, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


def current() -> str:
    """The operator the running work acts as; empty for the GitHub App."""
    return _acting.get()


def use(identity: str | None) -> None:
    """From here on, this piece of work acts as the operator; empty or None for the GitHub App."""
    _acting.set(identity or "")


@contextlib.contextmanager
def as_operator(identity: str | None) -> Iterator[None]:
    """Acts as the operator inside the block."""
    token = _acting.set(identity or "")
    try:
        yield
    finally:
        _acting.reset(token)


def of_goal(goal_id: str | None) -> str:
    """The operator the goal acts as; empty when it has none, or there is no goal."""
    if not goal_id:
        return ""
    held = _held.get(goal_id)
    if held and time.monotonic() - held[0] < HELD_SECONDS:
        return held[1]
    with db.connect(autocommit=True) as conn:
        row = conn.execute("SELECT acts_as FROM planner.goal WHERE id = %s", (goal_id,)).fetchone()
    who = (row[0] if row else "") or ""
    _held[goal_id] = (time.monotonic(), who)
    return who


def of_task(task_id: str | None) -> str:
    """The operator the task's goal acts as; empty when the task has no goal, or the goal has none."""
    if not task_id:
        return ""
    with db.connect(autocommit=True) as conn:
        row = conn.execute("SELECT goal_id FROM tasks.task WHERE id = %s", (task_id,)).fetchone()
    return of_goal(row[0] if row else None)


def of_request(request: Any) -> str:
    """The operator an activity's request acts as, from the goal or the task it names."""
    goal_id = getattr(request, "goal_id", "") or ""
    if goal_id:
        return of_goal(goal_id)
    return of_task(getattr(request, "task_id", "") or "")


def set_for(goal_id: str, identity: str) -> None:
    """Record the operator a goal acts as."""
    with db.connect(autocommit=True) as conn:
        conn.execute("UPDATE planner.goal SET acts_as = %s WHERE id = %s", (identity, goal_id))
    _held.pop(goal_id, None)


def connected(identity: str) -> bool:
    """Whether the operator has a working GitHub connection."""
    from meta_harness.planner import github_api

    try:
        return github_api.token_of(identity) is not None
    except github_user.GitHubUserError:
        return False


def token(identity: str) -> str:
    """The operator's GitHub token.

    Raises:
        ActingError: They have no working connection.
    """
    from meta_harness.planner import github_api

    try:
        found = github_api.token_of(identity)
    except github_user.GitHubUserError as err:
        raise ActingError(str(err)) from err
    if found is None:
        raise ActingError(
            f"{identity} has no working GitHub connection, and this goal acts as them. "
            "Connect GitHub again in Settings, then retry"
        )
    return found


async def call(
    identity: str, method: str, path: str, transport: httpx.AsyncBaseTransport | None = None, **kwargs: Any
) -> Any:
    """A REST call to GitHub as the operator. Returns the parsed body.

    Raises:
        ActingError: The call failed. A refused token marks the connection broken, and the message says to
            connect again.
    """
    from meta_harness.planner import github_api

    async with httpx.AsyncClient(
        base_url=github_app.API,
        timeout=httpx.Timeout(30.0, connect=10.0),
        transport=retrying.async_transport(transport),
        headers={
            "Authorization": f"token {token(identity)}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    ) as http:
        try:
            return await github_app._call(http, method, path, **kwargs)
        except github_app.GitHubAppError as err:
            if err.status == 401:
                github_api.mark_broken(identity)
                raise ActingError(
                    f"GitHub no longer accepts {identity}'s connection, and this goal acts as them. "
                    "Connect GitHub again in Settings, then retry",
                    status=401,
                ) from err
            raise ActingError(str(err), retryable=err.retryable, status=err.status) from err


async def github(apps: Any, repo: str, permissions: dict[str, str], method: str, path: str, **kwargs: Any) -> Any:
    """A REST call for the repo: as the operator the running work acts as, else on the GitHub App's
    installation token with these permissions. Either way a failure is a GitHubAppError, so a caller handles
    one kind."""
    if not (who := current()):
        return await apps.call(repo, permissions, method, path, **kwargs)
    try:
        return await call(who, method, path, **kwargs)
    except ActingError as err:
        raise github_app.GitHubAppError(str(err), retryable=err.retryable, status=err.status) from err
