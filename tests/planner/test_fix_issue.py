# Created by the meta-harness in run t-ab2c67f94b85:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""Fix with AI: the planner drafts what a marked issue lacks, and writes it to the tracker once accepted
(planner/fix_issue.py).

The planner's API runs against Postgres with a fake HTTP Gateway, a fake tracker and a fake Task API. No model,
no GitHub.
"""

import dataclasses
import functools
import json

import httpx
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

from meta_harness import db, outbound
from meta_harness.costs import model_source
from meta_harness.planner import fix_issue
from meta_harness.planner.service import create_app
from meta_harness.planner.tracker import Issue, acceptance_checks, issue_paths
from meta_harness.run_service import provenance
from meta_harness.trackers.base import check_section, with_section

from .test_planner import OPERATOR, Events, FakeTasks, _no_title, _number, migrated, registry  # noqa: F401
from .test_planning import WritingTracker
from .test_sizing import REPO, FakeSizer, _goal, _payloads, _sweep, _task

MODEL_ANSWER = {
    "acceptance_checks": ["uv run pytest -q tests/planner/test_fix_issue.py", " `uv run ruff check .` "],
    "paths": [],
    "testable_outcome": "",
}


class SectionTracker(WritingTracker):
    """A tracker that adds sections to a description and labels issues, recording each call."""

    def __init__(self):
        super().__init__()
        self.sections: list[tuple[str, str, str]] = []
        self.marked: list[str] = []

    async def add_section(self, ref, heading, text):
        outbound.require(heading, "a section's heading", outbound.GITHUB, title=True)
        outbound.require(text, "a section of an issue's body", outbound.GITHUB)
        issue = self.issues[ref]
        check_section(ref, issue.body, heading)
        body = with_section(issue.body, heading, text)
        self.issues[ref] = dataclasses.replace(
            issue, body=body, checks=acceptance_checks(body), paths=issue_paths(body), updated_at=f"edited-{ref}"
        )
        self.sections.append((ref, str(heading), str(text)))

    async def mark(self, ref):
        self.marked.append(ref)


@pytest.fixture
def world(monkeypatch):
    monkeypatch.delenv("MH_CONFIG_SOURCE", raising=False)
    events, tracker, tasks, sizer = Events(), SectionTracker(), FakeTasks(), FakeSizer()
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


@pytest.fixture
def gateway(monkeypatch):
    """A fake HTTP Gateway for the draft's model call: it answers what the test sets and keeps each request."""
    monkeypatch.setenv("GATEWAY_URL", "http://gateway")
    monkeypatch.setenv("GATEWAY_API_KEY", "gw-key")
    state = {"status": 200, "answer": MODEL_ANSWER, "seen": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["seen"].append(json.loads(request.content))
        if state["status"] != 200:
            return httpx.Response(state["status"], text="the model is down")
        return httpx.Response(200, json={"data": state["answer"], "finish_reason": "complete", "usd_cost": "0.0021"})

    monkeypatch.setattr(
        fix_issue, "draft_fix", functools.partial(fix_issue.draft_fix, transport=httpx.MockTransport(handler))
    )
    return state


def _held_without_checks(client, tracker) -> tuple[dict, str]:
    """A strict goal holding an issue that has a Paths: line and no acceptance checks, beside one complete issue
    whose paths the draft is shown."""
    goal = _goal(client, strict_issues=True)
    tracker.add(
        REPO,
        _number(),
        body="Done.\n\nPaths: src/meta_harness/planner\n\n## Acceptance checks\n\n- make test",
        labels=[goal["label"]],
    )
    ref = tracker.add(REPO, _number(), body="Fix the sizing card.\n\nPaths: console/src", labels=[goal["label"]])
    _sweep(client, goal)
    return goal, ref


def test_the_shipped_fixing_settings_pass_their_check(monkeypatch):
    fix_issue.check_settings()
    monkeypatch.setattr(fix_issue, "settings", lambda: {"engine": "claude", "model": "", "body_chars": 1})
    with pytest.raises(RuntimeError, match="issue_fixing.model"):
        fix_issue.check_settings()


def test_fix_draft_returns_checks_for_an_issue_missing_them_and_writes_nothing(world, gateway):
    client, events, tracker, tasks, sizer = world
    goal, ref = _held_without_checks(client, tracker)
    body_before = tracker.issues[ref].body

    drafted = client.post(f"/v1/goals/{goal['id']}/issues/fix-draft", json={"ref": ref}, headers=OPERATOR)

    assert drafted.status_code == 200, drafted.text
    [draft] = drafted.json()["drafts"]
    assert (draft["part"], draft["heading"], draft["doubted"]) == ("acceptance_checks", "Acceptance checks", False)
    assert draft["text"] == "- `uv run pytest -q tests/planner/test_fix_issue.py`\n- `uv run ruff check .`"
    # The model was asked for the missing part, with the issue and the paths the goal's other issues name.
    [asked] = gateway["seen"]
    assert (asked["engine"], asked["model"]) == (fix_issue.settings()["engine"], fix_issue.settings()["model"])
    assert "- acceptance_checks: missing" in asked["prompt"] and "Fix the sizing card." in asked["prompt"]
    assert "src/meta_harness/planner" in asked["prompt"]
    # Nothing was written to the tracker.
    assert tracker.sections == [] and tracker.marked == [] and tracker.issues[ref].body == body_before


def test_the_drafts_cost_is_recorded_under_the_goal(world, gateway):
    client, events, tracker, tasks, sizer = world
    goal, ref = _held_without_checks(client, tracker)

    assert client.post(f"/v1/goals/{goal['id']}/issues/fix-draft", json={"ref": ref}, headers=OPERATOR).is_success

    [stored] = [e for e in events.stored.values() if e["event_type"] == "issue_fix_drafted"]
    assert stored["payload"] == {
        "goal_id": goal["id"],
        "ref": ref,
        "parts": ["acceptance_checks"],
        "model": "claude:claude-haiku-4-5",
        "usd_cost": "0.0021",
    }
    assert stored["tags"] == {"goal_id": goal["id"]} and stored["actor"] == "dave"

    # The cost harvest counts it as a planning cost of the goal.
    class Recorded:
        def read(self, from_position=0, limit=500, **filters):
            event = {**stored, "position": 1, "time_utc": "2026-11-10T12:00:00+00:00"}
            wanted = event["event_type"] in filters["event_type"].split(",") and from_position < 1
            return {"events": [event] if wanted else []}

    owner = {**goal, "created_by": "dave@example.com"}
    with db.connect(autocommit=True) as conn:
        conn.execute("DELETE FROM costs.daily_cost")
        conn.execute("DELETE FROM costs.harvest")
        assert model_source.harvest(conn, Recorded(), lambda _: None, {goal["id"]: owner}.get) == 1
        rows = conn.execute("SELECT item, goal_id, repo, model, engine, operator, usd FROM costs.daily_cost")
        assert rows.fetchall() == [
            ("planning", goal["id"], REPO, "claude-haiku-4-5", "claude", "dave@example.com", Decimal("0.0021"))
        ]


def test_fix_accept_adds_the_section_labels_the_issue_and_records_a_new_sizing(world):
    client, events, tracker, tasks, sizer = world
    goal, ref = _held_without_checks(client, tracker)
    assert client.get(f"/v1/goals/{goal['id']}/issues", headers=OPERATOR).json()["issues"]
    sized_before = len(_payloads(events, "issue_sized"))

    accepted = client.post(
        f"/v1/goals/{goal['id']}/issues/fix-accept",
        json={"ref": ref, "parts": [{"part": "acceptance_checks", "text": "uv run pytest -q tests/planner"}]},
        headers=OPERATOR,
    )

    assert accepted.status_code == 200, accepted.text
    [(written, heading, text)] = tracker.sections
    assert (written, heading) == (ref, "Acceptance checks")
    assert text.startswith("- uv run pytest -q tests/planner\n\nProvenance: meta-harness ")
    assert (provenance.parse(text)[-1]["goal"], provenance.parse(text)[-1]["by"]) == (goal["id"], "dave")
    assert tracker.marked == [ref]
    assert tracker.issues[ref].checks == ["uv run pytest -q tests/planner"]
    # Sized again: the new sizing is on the link and recorded, and the issue lacks nothing now.
    assert accepted.json()["added"] == ["Acceptance checks"] and accepted.json()["sizing"]["missing"] == []
    assert sizer.calls.count(ref) == 2
    assert len(_payloads(events, "issue_sized")) == sized_before + 1
    assert _payloads(events, "issue_sized")[-1]["ref"] == ref
    card = {i["ref"]: i for i in client.get(f"/v1/goals/{goal['id']}/issues", headers=OPERATOR).json()["issues"]}
    assert card[ref]["missing"] == [] and card[ref]["checks_named"] == ["uv run pytest -q tests/planner"]
    # The strict goal held it for the gap; with the gap filled, a sweep makes it a task.
    _sweep(client, goal)
    assert _task(tasks, ref) is not None


def test_fix_accept_on_an_issue_with_a_running_task_answers_409_and_writes_nothing(world, gateway):
    client, events, tracker, tasks, sizer = world
    goal = _goal(client)
    ref = tracker.add(REPO, _number(), body="Fix the sizing card.\n\nPaths: console/src", labels=[goal["label"]])
    _sweep(client, goal)
    task = _task(tasks, ref)
    tasks.set_status(task["id"], "running")
    body_before = tracker.issues[ref].body

    accepted = client.post(
        f"/v1/goals/{goal['id']}/issues/fix-accept",
        json={"ref": ref, "parts": [{"part": "acceptance_checks", "text": "make test"}]},
        headers=OPERATOR,
    )
    drafted = client.post(f"/v1/goals/{goal['id']}/issues/fix-draft", json={"ref": ref}, headers=OPERATOR)

    assert accepted.status_code == 409, accepted.text
    assert "can be accepted once the task ends" in accepted.json()["detail"]
    assert drafted.status_code == 409 and gateway["seen"] == [], "no model is paid for a change it cannot take"
    assert tracker.sections == [] and tracker.marked == [] and tracker.issues[ref].body == body_before
    assert _payloads(events, "issue_fix_drafted") == []

    # Once the task ends, the change is taken.
    tasks.set_status(task["id"], "failed")
    taken = client.post(
        f"/v1/goals/{goal['id']}/issues/fix-accept",
        json={"ref": ref, "parts": [{"part": "acceptance_checks", "text": "- `make test`"}]},
        headers=OPERATOR,
    )
    assert taken.status_code == 200, taken.text
    assert [h for _, h, _ in tracker.sections] == ["Acceptance checks"]


def test_a_draft_the_model_cannot_give_answers_502_and_changes_nothing(world, gateway):
    client, events, tracker, tasks, sizer = world
    goal, ref = _held_without_checks(client, tracker)
    gateway["status"] = 500

    drafted = client.post(f"/v1/goals/{goal['id']}/issues/fix-draft", json={"ref": ref}, headers=OPERATOR)

    assert drafted.status_code == 502 and "Try Fix with AI again" in drafted.json()["detail"]
    assert tracker.sections == [] and _payloads(events, "issue_fix_drafted") == []


def test_a_part_with_nothing_in_it_or_an_issue_with_nothing_marked_is_refused(world, gateway):
    client, events, tracker, tasks, sizer = world
    goal, ref = _held_without_checks(client, tracker)
    url = f"/v1/goals/{goal['id']}/issues"

    blank = client.post(
        f"{url}/fix-accept", json={"ref": ref, "parts": [{"part": "paths", "text": " \n "}]}, headers=OPERATOR
    )
    assert blank.status_code == 422 and "paths is empty" in blank.json()["detail"]
    # Its Paths: line is there, so a doubted paths part goes under its own heading; a missing one would clash.
    doubled = client.post(
        f"{url}/fix-accept",
        json={"ref": ref, "parts": [{"part": "paths", "text": "a"}, {"part": "paths", "text": "b"}]},
        headers=OPERATOR,
    )
    assert doubled.status_code == 422
    assert tracker.sections == []

    complete = tracker.add(
        REPO, _number(), body="Done.\n\nPaths: docs\n\n## Acceptance checks\n\n- make test", labels=[goal["label"]]
    )
    _sweep(client, goal)
    nothing = client.post(f"{url}/fix-draft", json={"ref": complete}, headers=OPERATOR)
    assert nothing.status_code == 409 and "marks nothing" in nothing.json()["detail"]

    # A section the description has already is refused before anything is written.
    vague = tracker.add(REPO, _number(), body="Fix it.\n\nPaths: docs\n\n## Outcome\n\nBetter.", labels=[goal["label"]])
    sizer.answers[vague] = {"size": "small", "reasons": [], "missing": ["testable_outcome"], "split": []}
    _sweep(client, goal)
    clash = client.post(
        f"{url}/fix-accept",
        json={
            "ref": vague,
            "parts": [{"part": "acceptance_checks", "text": "make test"}, {"part": "testable_outcome", "text": "x"}],
        },
        headers=OPERATOR,
    )
    assert clash.status_code == 409 and "already has a section 'Outcome'" in clash.json()["detail"]
    assert tracker.sections == [] and tracker.marked == []


def test_a_doubted_part_goes_under_its_own_heading_and_paths_get_a_paths_line():
    issue = Issue(ref="github:o/r#1", repo="o/r", number=1, title="t", body="", labels=[], state="open")
    issue.checks = ["make test"]
    assert fix_issue.heading_for("acceptance_checks", issue) == "More acceptance checks"
    assert fix_issue.heading_for("paths", issue) == "Paths"
    assert fix_issue.section_text("paths", "src/a,\n docs") == "Paths: src/a, docs"
    assert fix_issue.section_text("paths", "Paths: src/a") == "Paths: src/a"
    assert fix_issue.section_text("acceptance_checks", "make test\n* make lint") == "- make test\n* make lint"
    sized = {"missing": ["testable_outcome"], "doubts": [{"part": "acceptance_checks", "reason": "old parser"}]}
    assert fix_issue.marked_parts(sized) == {"acceptance_checks": "old parser", "testable_outcome": ""}
