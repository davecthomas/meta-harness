"""A client for the planner's goal endpoints, for the `mh` command line and the console web app."""

# The class has a method named list, so annotations must not be evaluated inside its body.
from __future__ import annotations

import os
from typing import Any

import httpx

from meta_harness.tasks.client import TaskApiError

DEFAULT_URL = "http://127.0.0.1:8303"


class GoalsClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 60.0) -> None:
        self.base_url = (base_url or os.environ.get("MH_PLANNER_URL", DEFAULT_URL)).rstrip("/")
        key = api_key or os.environ.get("MH_OPERATOR_KEY", "")
        self.http = httpx.Client(base_url=self.base_url, timeout=timeout, headers={"Authorization": f"Bearer {key}"})

    def _call(self, method: str, path: str, actor: str | None = None, **kwargs: Any) -> Any:
        headers = {"X-MH-Actor": actor} if actor else None
        response = self.http.request(method, path, headers=headers, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise TaskApiError(response.status_code, str(detail))
        return response.json()

    # `actor` names the operator a service key acts for; the console passes the signed-in operator.

    def create(self, actor: str | None = None, **goal: Any) -> dict:
        return self._call("POST", "/v1/goals", actor=actor, json=goal)

    def list(self, statuses: list[str] | None = None) -> list[dict]:
        params = {"status": ",".join(statuses)} if statuses else {}
        return self._call("GET", "/v1/goals", params=params)["goals"]

    def get(self, goal_id: str) -> dict:
        return self._call("GET", f"/v1/goals/{goal_id}")

    def patch(self, goal_id: str, actor: str | None = None, **fields: Any) -> dict:
        return self._call("PATCH", f"/v1/goals/{goal_id}", actor=actor, json=fields)

    def action(self, goal_id: str, name: str, actor: str | None = None) -> dict:
        return self._call("POST", f"/v1/goals/{goal_id}/{name}", actor=actor)

    def repos(self) -> list[dict]:
        return self._call("GET", "/v1/repos")["repos"]
