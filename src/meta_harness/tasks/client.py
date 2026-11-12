"""A Task API client, for callers, the `mh` command line and control-plane services."""

# The class has a method named list, so annotations must not be evaluated inside its body.
from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:8302"


class TaskApiError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Task API {status}: {detail}")
        self.status = status
        self.detail = detail


class TaskClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = (base_url or os.environ.get("MH_TASKS_URL", DEFAULT_URL)).rstrip("/")
        key = api_key or os.environ.get("MH_TASK_API_KEY", "")
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

    def create(self, **task: Any) -> dict:
        return self._call("POST", "/v1/tasks", json=task)

    def get(self, task_id: str) -> dict:
        return self._call("GET", f"/v1/tasks/{task_id}")

    def find(self, external_ref: str) -> dict:
        return self._call("GET", "/v1/tasks", params={"external_ref": external_ref})

    def list(self, statuses: list[str] | None = None, goal_id: str | None = None, limit: int = 100) -> list[dict]:
        return self.page(statuses, goal_id, limit, 0)["tasks"]

    def pending_messages(self) -> list[dict]:
        """Operator messages waiting to be handed to their task's workflow."""
        return self._call("GET", "/v1/messages/pending")["messages"]

    def mark_delivered(self, message_id: int) -> None:
        self._call("POST", f"/v1/messages/{message_id}/delivered")

    def search(self, q: str, limit: int = 10) -> list[dict]:
        """Tasks whose title, id or issue ref holds the text."""
        return self._call("GET", "/v1/tasks", params={"q": q, "limit": limit})["tasks"]

    def page(
        self, statuses: list[str] | None = None, goal_id: str | None = None, limit: int = 25, offset: int = 0
    ) -> dict:
        """One page of tasks: {tasks, total, offset, limit}. Deploys' smoke tasks included: this client is the
        services', which must see every task; the console's own lists leave them out."""
        params: dict[str, Any] = {"limit": limit, "offset": offset, "smoke": "true"}
        if statuses:
            params["status"] = ",".join(statuses)
        if goal_id:
            params["goal_id"] = goal_id
        return self._call("GET", "/v1/tasks", params=params)

    def cancel(self, task_id: str, reason: str = "canceled through the Task API", actor: str | None = None) -> dict:
        return self._call("POST", f"/v1/tasks/{task_id}/cancel", actor=actor, json={"reason": reason})

    def recover(self, task_id: str, why: str, spent_usd: str = "0") -> dict:
        """End the wait of a running or admitted task with no workflow behind it: back to the queue, canceled
        when it was asked to cancel, or failed after too many recoveries. The supervisor's."""
        return self._call(
            "POST", f"/v1/tasks/{task_id}/recover", actor="supervisor", json={"why": why, "spent_usd": spent_usd}
        )

    def drive(self, task_id: str, action: str, why: str, spent_usd: str = "0", counted: bool = True) -> dict:
        """Send a parked task back to the queue for its merge (action merge) or through its review loop
        (review), or a failed one to run again (retry), with what it has spent carried over. counted False:
        not one of the task's tries. The supervisor's."""
        body = {"action": action, "why": why, "spent_usd": spent_usd, "counted": counted}
        return self._call("POST", f"/v1/tasks/{task_id}/drive", actor="supervisor", json=body)

    def budget(self, task_id: str, action: str, max_usd: str | None = None, actor: str | None = None) -> dict:
        """Answer a task held for budget: raise (with a new spend limit, or none when its goal's was raised) or end."""
        return self._call(
            "POST", f"/v1/tasks/{task_id}/budget", actor=actor, json={"action": action, "max_usd": max_usd}
        )

    def set_status(self, task_id: str, status: str, reason: str | None = None, **fields: Any) -> dict:
        return self._call("POST", f"/v1/tasks/{task_id}/status", json={"status": status, "reason": reason, **fields})
