"""`mh goal updates` prints the planner's updates from a fake client; no network call is made."""

import httpx
from click.testing import CliRunner

from meta_harness.cli import main
from meta_harness.planner import client as planner_client
from meta_harness.tasks.client import TaskApiError

NEW = {
    "number": 2,
    "created_at": "2025-01-02T10:00:00Z",
    "headline": "Two pull requests await review",
    "body": "Both fixes passed their checks.",
    "needs_you": [{"what": "Approve the merge", "ref": "owner/repo#7"}, {"what": "Pick a label", "ref": ""}],
    "next": "Sweep again tomorrow",
    "model": "m",
}
OLD = {
    "number": 1,
    "created_at": "2025-01-01T10:00:00Z",
    "headline": "Goal started",
    "body": "Took in three issues.",
    "needs_you": [],
    "next": "",
    "model": "m",
}


def _run(monkeypatch, result=None, error=None, args=()):
    class Goals:
        def updates(self, goal_id):
            if error:
                raise error
            return result

    monkeypatch.setattr(planner_client, "GoalsClient", Goals)
    return CliRunner().invoke(main, ["goal", "updates", "g-1", *args])


def test_updates_print_newest_first(monkeypatch):
    out = _run(monkeypatch, {"updates": [NEW, OLD], "enabled": True, "error": None})
    assert out.exit_code == 0, out.output
    text = out.output
    assert text.index("Two pull requests await review") < text.index("Goal started")
    assert "Both fixes passed their checks." in text and "Took in three issues." in text
    assert "Needs you:" in text
    assert "Approve the merge (owner/repo#7)" in text
    assert "Pick a label" in text
    assert "Next: Sweep again tomorrow" in text


def test_update_without_needs_you_has_no_line(monkeypatch):
    out = _run(monkeypatch, {"updates": [OLD], "enabled": True, "error": None})
    assert out.exit_code == 0
    assert "Needs you" not in out.output
    assert "Next:" not in out.output


def test_limit_keeps_first_updates(monkeypatch):
    out = _run(monkeypatch, {"updates": [NEW, OLD], "enabled": True, "error": None}, args=["--limit", "1"])
    assert "Two pull requests" in out.output and "Goal started" not in out.output


def test_no_updates(monkeypatch):
    out = _run(monkeypatch, {"updates": [], "enabled": True, "error": None})
    assert out.exit_code == 0
    assert "no updates yet" in out.output


def test_updates_off(monkeypatch):
    out = _run(monkeypatch, {"updates": [], "enabled": False, "error": None})
    assert "updates are off" in out.output


def test_recorded_error(monkeypatch):
    out = _run(monkeypatch, {"updates": [OLD], "enabled": True, "error": "model timed out"})
    assert "could not be written: model timed out" in out.output
    assert "Goal started" in out.output


def test_not_found(monkeypatch):
    out = _run(monkeypatch, error=TaskApiError(404, "goal g-1 not found"))
    assert out.exit_code != 0
    assert "goal g-1 not found" in out.output


def test_client_requests_updates_route():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"updates": [], "enabled": True, "error": None})

    client = planner_client.GoalsClient(base_url="http://planner.test", api_key="k")
    client.http = httpx.Client(base_url="http://planner.test", transport=httpx.MockTransport(handler))
    assert client.updates("g-1")["enabled"] is True
    assert seen == [("GET", "/v1/goals/g-1/updates")]
