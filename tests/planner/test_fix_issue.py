# Created by the meta-harness in run t-f39d0d66479d:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""Fix with AI: the planner drafts what a goal's issue lacks, and writes it to the tracker once accepted.

The planner's API runs against Postgres with a fake gateway, a fake tracker and a fake Task API. No model,
no GitHub and no run is called.
"""

import dataclasses
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from meta_harness import db, outbound
from meta_harness.costs import model_source
from meta_harness.planner import fix_issue
from meta_harness.planner.service import create_app
from meta_harness.planner.tracker import acceptance_checks, issue_paths
from meta_harness.trackers.base import check_section, with_section

from .test_planner import OPERATOR, Events, FakeTasks, _no_title, _number, migrated, registry  # noqa: F401
from .test_planning import WritingTracker
from .test_sizing import REPO, FakeSizer, _goal, _payloads, _shown, _sweep, _task

pytestmark = pytest.mark.db


class FixTracker(WritingTracker):
    """A tracker that adds sections to an issue's description and labels issues, as the adapters do."""

    def __init__(self):
        super().__init__()
        self.sections: list[tuple[str, str, str]] = []
        self.marked: list[str] = []

    async def mark(self, ref):
        self.marked.append(ref)

    async def add_section(self, ref, heading, text):
        outbound.require(heading, "a section's heading", outbound.GITHUB, title=True)
        outbound.require(text, "a section of an issue's body", outbound.GITHUB)
        issue = self.issues[ref]
        check_section(ref, issue.body, heading)
        body = with_section(issue.body, heading, text)
        self.issues[ref] = dataclasses.replace(
            issue,
            body=body,
            checks=acceptance_checks(body),
            paths=issue_paths(body),
            updated_at=datetime.now(UTC).isoformat(),
        )
        self.sections.append((ref, str(heading), str(text)))


@pytest.fixture
def world(monkeypatch):
    monkeypatch.delenv("MH_CONFIG_SOURCE", raising=False)
    events, tracker, tasks, sizer = Events(), FixTracker(), FakeTasks(), FakeSizer()
    app = create_app(
        events,
        tracker,
        tasks,
        operator_keys={"op-key": "dave"},
        service_keys={},
        sweep_seconds=0,
        titler=_no_title,
        sizer=sizer,
    )
    with TestClient(app) as client:
        yield client, events, tracker, tasks, sizer


class Gateway:
    """A fake HTTP Gateway for the draft's one call: it answers what the test sets, and keeps what it was sent."""

    def __init__(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_URL", "http://gateway")
        monkeypatch.setenv("GATEWAY_API_KEY", "gw-key")
        self.sent: list[dict] = []
        self.status = 200
        self.answer = {"acceptance_checks": ["uv run pytest -q tests/x"], "paths": [], "testable_outcome": ""}
        real = fix_issue.draft

        def draft(*args, **kwargs):
            return real(*args, transport=httpx.MockTransport(self.handler), **kwargs)

        monkeypatch.setattr(fix_issue, "draft", draft)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(json.loads(request.content))
        if self.status != 200:
            return httpx.Response(self.status, text="the provider failed")
        return httpx.Response(200, json={"data": self.answer, "finish_reason": "complete", "usd_cost": "0.0021"})


def _issue_missing_checks(client, tracker) -> tuple[dict, str]:
    """An active goal holding one issue with a Paths: line and no acceptance checks, sized by a sweep."""
    goal = _goal(client)
    ref = tracker.add(REPO, _number(), body="Fix the parser.\n\nPaths: src/parser", labels=[goal["label"]])
    _sweep(client, goal)
    assert _shown(client, goal)[ref]["missing"] == ["acceptance_checks"]
    return goal, ref


# What the drafts and the accepted text look like, without a database.


def test_the_parts_wanted_are_what_the_sizing_says_is_missing_or_doubted():
    record = {
        "missing": ["testable_outcome"],
        "doubts": [{"part": "acceptance_checks", "reason": "make test does not cover the parser"}],
    }
    assert fix_issue.wanted(record) == [
        {"part": "acceptance_checks", "reason": "make test does not cover the parser"},
        {"part": "testable_outcome", "reason": ""},
    ]
    assert fix_issue.wanted(None) == []


def test_a_part_goes_under_its_own_heading_or_its_second_where_the_description_has_the_first():
    assert fix_issue.heading_for("acceptance_checks", "Fix it.") == "Acceptance checks"
    assert fix_issue.heading_for("acceptance_checks", "## Acceptance checks\n\n- x") == "More acceptance checks"
    assert fix_issue.heading_for("paths", "## Paths\n\nPaths: a\n\n## More paths\n\nPaths: b") is None


def test_the_operators_text_is_shaped_so_the_planner_reads_it():
    assert fix_issue.normalized("acceptance_checks", "uv run pytest\n- `make lint`\n") == (
        "- `uv run pytest`\n- `make lint`"
    )
    assert fix_issue.normalized("paths", "src/a, ./src/b/\n") == "Paths: src/a, src/b"
    assert fix_issue.normalized("paths", "Paths: src/a") == "Paths: src/a"
    assert fix_issue.normalized("testable_outcome", "  ") == ""


def test_the_shipped_fixing_settings_pass_their_check_and_a_bad_one_is_named(monkeypatch):
    fix_issue.check_settings()
    shipped = fix_issue.settings()
    monkeypatch.setattr(fix_issue, "settings", lambda: {**shipped, "max_paths": 0})
    with pytest.raises(RuntimeError, match="issue_fixing.max_paths"):
        fix_issue.check_settings()


# The routes, with the planner's API on Postgres.


def test_fix_draft_returns_checks_for_an_issue_missing_them_and_writes_nothing(world, monkeypatch):
    client, events, tracker, tasks, sizer = world
    gateway = Gateway(monkeypatch)
    goal, ref = _issue_missing_checks(client, tracker)
    body_before = tracker.issues[ref].body

    drafted = client.post(f"/v1/goals/{goal['id']}/issues/fix-draft", json={"ref": ref}, headers=OPERATOR)

    assert drafted.status_code == 200, drafted.text
    [part] = drafted.json()["drafts"]
    assert part == {
        "part": "acceptance_checks",
        "reason": "",
        "text": "- `uv run pytest -q tests/x`",
        "heading": "Acceptance checks",
    }
    [sent] = gateway.sent
    config = fix_issue.settings()
    assert (sent["engine"], sent["model"]) == (config["engine"], config["model"])
    prompt = sent["prompt"]
    assert "Fix the parser." in prompt and '"part": "acceptance_checks"' in prompt
    # The repo's registered default checks are part of what the model reads.
    assert "uv run ruff check ." in prompt
    # Nothing is written: no section, no label, the body as it was.
    assert tracker.sections == [] and tracker.marked == []
    assert tracker.issues[ref].body == body_before
    assert "issue_fix_accepted" not in events.types()


def test_fix_accept_adds_the_section_labels_the_issue_and_records_a_new_sizing(world):
    client, events, tracker, tasks, sizer = world
    goal, ref = _issue_missing_checks(client, tracker)
    sized_before = len(_payloads(events, "issue_sized"))

    accepted = client.post(
        f"/v1/goals/{goal['id']}/issues/fix-accept",
        json={"ref": ref, "parts": [{"part": "acceptance_checks", "text": "uv run pytest -q tests/parser"}]},
        headers=OPERATOR,
    )

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["added"] == ["acceptance_checks"]
    assert accepted.json()["missing"] == [] and accepted.json()["sizing_error"] == ""
    assert tracker.sections == [(ref, "Acceptance checks", "- `uv run pytest -q tests/parser`")]
    assert tracker.issues[ref].checks == ["uv run pytest -q tests/parser"]
    assert tracker.marked == [ref], "the issue is labeled as the meta-harness's"
    assert _payloads(events, "issue_fix_accepted") == [
        {"goal_id": goal["id"], "ref": ref, "parts": ["acceptance_checks"]}
    ]
    sized = _payloads(events, "issue_sized")
    assert len(sized) == sized_before + 1 and sized[-1]["ref"] == ref and sized[-1]["missing"] == []
    assert sizer.calls.count(ref) == 2, "sized by the sweep, then again after the fix"
    _sweep(client, goal)
    assert _shown(client, goal)[ref]["missing"] == []
    assert _shown(client, goal)[ref]["checks_named"] == ["uv run pytest -q tests/parser"]


def test_fix_accept_on_an_issue_whose_task_is_running_answers_409_and_writes_nothing(world):
    client, events, tracker, tasks, sizer = world
    goal, ref = _issue_missing_checks(client, tracker)
    task = _task(tasks, ref)
    tasks.tasks[task["id"]]["status"] = "running"

    refused = client.post(
        f"/v1/goals/{goal['id']}/issues/fix-accept",
        json={"ref": ref, "parts": [{"part": "acceptance_checks", "text": "make test"}]},
        headers=OPERATOR,
    )

    assert refused.status_code == 409
    assert "can be accepted once the task ends" in refused.json()["detail"]
    assert tracker.sections == [] and tracker.marked == []
    assert "issue_fix_accepted" not in events.types()


def test_the_drafts_cost_is_recorded_under_the_goal(world, monkeypatch):
    client, events, tracker, tasks, sizer = world
    Gateway(monkeypatch)
    goal, ref = _issue_missing_checks(client, tracker)

    drafted = client.post(f"/v1/goals/{goal['id']}/issues/fix-draft", json={"ref": ref}, headers=OPERATOR)
    assert drafted.status_code == 200, drafted.text

    [event] = [e for e in events.stored.values() if e["event_type"] == "issue_fix_drafted"]
    assert event["payload"] == {
        "goal_id": goal["id"],
        "ref": ref,
        "parts": ["acceptance_checks"],
        "model": f"{fix_issue.settings()['engine']}:{fix_issue.settings()['model']}",
        "usd_cost": "0.0021",
    }
    assert event["tags"] == {"goal_id": goal["id"]}

    # The cost harvest counts it as a planning cost of the goal.
    class Recorded:
        def read(self, from_position=0, limit=500, event_type="", **filters):
            found = [
                {**event, "position": 1, "time_utc": "2026-11-10T12:00:00+00:00"}
                for _ in [0]
                if from_position < 1 and event["event_type"] in event_type.split(",")
            ]
            return {"events": found}

    owner = {"id": goal["id"], "created_by": "dave", "repos": [REPO]}
    with db.connect(autocommit=True) as conn:
        conn.execute("DELETE FROM costs.harvest")
        conn.execute("DELETE FROM costs.daily_cost WHERE goal_id = %s", (goal["id"],))
        assert model_source.harvest(conn, Recorded(), lambda _: None, lambda g: owner if g == goal["id"] else None)
        rows = conn.execute(
            "SELECT item, goal_id, model, usd FROM costs.daily_cost WHERE goal_id = %s", (goal["id"],)
        ).fetchall()
        conn.execute("DELETE FROM costs.daily_cost WHERE goal_id = %s", (goal["id"],))
    assert rows == [("planning", goal["id"], fix_issue.settings()["model"], Decimal("0.0021"))]


def test_a_draft_the_model_cannot_make_answers_502_and_changes_nothing(world, monkeypatch):
    client, events, tracker, tasks, sizer = world
    gateway = Gateway(monkeypatch)
    gateway.status = 500
    goal, ref = _issue_missing_checks(client, tracker)

    failed = client.post(f"/v1/goals/{goal['id']}/issues/fix-draft", json={"ref": ref}, headers=OPERATOR)

    assert failed.status_code == 502 and "500" in failed.json()["detail"]
    assert tracker.sections == [] and tracker.marked == []
    assert "issue_fix_drafted" not in events.types()


def test_an_issue_with_nothing_to_fix_or_not_the_goals_is_refused(world):
    client, events, tracker, tasks, sizer = world
    goal = _goal(client)
    whole = tracker.add(
        REPO, _number(), body="Fix it.\n\nPaths: src\n\n## Acceptance checks\n\n- make test", labels=[goal["label"]]
    )
    _sweep(client, goal)
    url = f"/v1/goals/{goal['id']}/issues/fix-draft"

    assert client.post(url, json={"ref": whole}, headers=OPERATOR).status_code == 409
    other = f"github:{REPO}#{uuid.uuid4().int % 10**9 + 10**12}"
    assert client.post(url, json={"ref": other}, headers=OPERATOR).status_code == 404
