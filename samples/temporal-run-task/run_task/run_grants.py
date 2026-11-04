"""Issues, reads and revokes run grants on ai-api-unified-http (the HTTP Gateway).

A run grant is the HTTP Gateway's record of what one run may do: one engine
and model, a spend limit, and an expiry. Issuing one returns a grant key. The
worker hands only the grant key to the harness server, so pi never sees the
gateway's API key, cannot pick its own model, and stops at the spend limit.

The worker authenticates with GATEWAY_API_KEY. In the full design the harness
engine in ai-api-unified does this; here run_harness stands in for it.

Environment: GATEWAY_URL (default http://localhost:8080), GATEWAY_API_KEY.
"""

import os
from typing import Any

import httpx

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8080")
REQUEST_TIMEOUT_SECONDS = 10.0


class RunGrantError(RuntimeError):
    """The HTTP Gateway refused or could not serve a run-grant request."""

    def __init__(self, message: str, status: int | None) -> None:
        super().__init__(message)
        self.status = status


def gateway_key() -> str | None:
    return os.environ.get("GATEWAY_API_KEY")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=GATEWAY_URL,
        headers={"Authorization": f"Bearer {gateway_key() or ''}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    return str(body.get("detail") or body.get("error") or body)


async def issue(
    run_id: str,
    engine: str,
    model: str,
    spend_limit_usd: str,
    ttl_seconds: int,
    workflow_id: str,
    tags: dict[str, str],
) -> dict[str, Any]:
    """POST /v1/run-grants. Asking again for a live run returns a new key for the same grant."""
    body = {
        "run_id": run_id,
        "engine": engine,
        "model": model,
        "spend_limit_usd": spend_limit_usd,
        "ttl_seconds": ttl_seconds,
        "workflow_id": workflow_id,
        "session_id": workflow_id,
        "tags": tags,
    }
    try:
        async with _client() as client:
            response = await client.post("/v1/run-grants", json=body)
    except httpx.HTTPError as err:
        raise RunGrantError(f"no HTTP Gateway at {GATEWAY_URL}: {err}", None) from err
    if response.status_code != 200:
        raise RunGrantError(
            f"the HTTP Gateway refused the run grant ({response.status_code}): {_detail(response)}",
            response.status_code,
        )
    return response.json()


async def get(run_id: str) -> dict[str, Any] | None:
    """GET /v1/run-grants/{run_id}, or None when the grant is gone or the gateway is unreachable."""
    try:
        async with _client() as client:
            response = await client.get(f"/v1/run-grants/{run_id}")
    except httpx.HTTPError:
        return None
    return response.json() if response.status_code == 200 else None


async def revoke(run_id: str) -> dict[str, Any]:
    """DELETE /v1/run-grants/{run_id}. Revoking a grant that is gone succeeds with revoked false."""
    try:
        async with _client() as client:
            response = await client.delete(f"/v1/run-grants/{run_id}")
    except httpx.HTTPError as err:
        raise RunGrantError(f"no HTTP Gateway at {GATEWAY_URL}: {err}", None) from err
    if response.status_code != 200:
        raise RunGrantError(
            f"the HTTP Gateway did not revoke the run grant ({response.status_code}): {_detail(response)}",
            response.status_code,
        )
    return response.json()
