"""A Task API client, for callers, the `mh` command line and control-plane services."""

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

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.http.request(method, path, **kwargs)
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
        params: dict[str, Any] = {"limit": limit}
        if statuses:
            params["status"] = ",".join(statuses)
        if goal_id:
            params["goal_id"] = goal_id
        return self._call("GET", "/v1/tasks", params=params)["tasks"]

    def cancel(self, task_id: str, reason: str = "canceled through the Task API") -> dict:
        return self._call("POST", f"/v1/tasks/{task_id}/cancel", json={"reason": reason})

    def set_status(self, task_id: str, status: str, reason: str | None = None, **fields: Any) -> dict:
        return self._call("POST", f"/v1/tasks/{task_id}/status", json={"status": status, "reason": reason, **fields})
