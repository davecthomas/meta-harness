"""Issue sizing: an issue is sized before it becomes a task, and a large one is held to split (sizing.py)."""

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from meta_harness.config import kinds
from meta_harness.planner import sizing
from meta_harness.planner.service import create_app
from meta_harness.planner.tracker import Issue

from .test_planner import OPERATOR, Events, FakeTasks, _label, _no_title, _number, migrated, registry  # noqa: F401
from .test_planning import WritingTracker

REPO = "davecthomas/meta-harness"
CHECKS = "## Acceptance checks\n\n- make test"


def _issue(body: str = "", title: str = "Fix it", paths: tuple = (), checks: tuple = ()) -> Issue:
    return Issue(
        ref=f"github:{REPO}#1",
        repo=REPO,
        number=1,
        title=title,
        body=body,
        labels=[],
        state="open",
        paths=list(paths),
        checks=list(checks),
    )


# What the model, the planner and the task policy decide, without a model or a database.


def test_the_answer_adds_what_the_planner_sees_missing_and_drops_what_it_does_not_know():
    data = {"size": "medium", "reasons": [" two modules ", ""], "missing": ["testable_outcome", "vibes"], "split": []}
    sized = sizing.parse(data, _issue(checks=("make test",)), "openai-decisions:m", "0.0004")

    assert sized.size == "medium" and sized.reasons == ["two modules"]
    assert sized.missing == ["paths", "testable_outcome"], "no Paths: line is missing whatever the model said"
    assert (sized.model, sized.usd_cost) == ("openai-decisions:m", "0.0004")
    with pytest.raises(sizing.SizingError, match="huge"):
        sizing.parse({**data, "size": "huge"}, _issue(), "m", "0")


def test_a_large_issue_is_held_with_its_split_and_a_gap_holds_only_a_strict_goals_issue():
    large = {"reasons": ["22 files"], "missing": [], "split": ["Edit titles", "Edit labels"]}
    held = sizing.hold_reason("large", large, strict=False)
    assert held and "sized large: 22 files" in held and "Suggested split: Edit titles; Edit labels" in held

    gap = {"reasons": [], "missing": ["paths"], "split": []}
    assert sizing.hold_reason("small", gap, strict=False) is None
    assert "missing a Paths: line" in sizing.hold_reason("small", gap, strict=True)
    assert sizing.hold_reason(None, None, strict=True) is None, "an issue not sized is never held"


def test_an_issue_names_its_own_spend_limit_on_a_line():
    assert sizing.issue_limit("Fix it.\n\nSpend limit: $4.50\n") == "4.50"
    assert sizing.issue_limit("spend limit: 12") == "12"
    assert sizing.issue_limit("Spend limit: none") is None
    assert sizing.issue_limit("") is None


def test_the_task_policy_gives_a_positive_limit_for_each_size_it_names():
    policy = json.loads((sizing.REPO_ROOT / "config" / "task_policy.json").read_text())
    assert kinds.check_task_policy(policy) == []
    assert kinds.check_task_policy({k: v for k, v in policy.items() if k != "max_usd_by_size"}) == []
    reasons = kinds.check_task_policy({**policy, "max_usd_by_size": {"huge": "1", "small": "0", "large": "x"}})
    assert len(reasons) == 3
    assert any("huge is not an issue size" in r for r in reasons)
    assert any("max_usd_by_size.small" in r for r in reasons) and any("max_usd_by_size.large" in r for r in reasons)
    assert kinds.check_task_policy({**policy, "max_usd_by_size": ["5"]})


def test_the_shipped_sizing_settings_pass_their_check():
    sizing.check_settings()
    assert sizing.settings()["engine"] == "openai-decisions", "the classifier's engine"


async def test_sizing_is_one_structured_call_with_the_configured_model(monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", "http://gateway")
    monkeypatch.setenv("GATEWAY_API_KEY", "gw-key")
    seen = []
    answer = {"size": "large", "reasons": ["many files"], "missing": [], "split": ["One", "Two"]}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"data": answer, "finish_reason": "complete", "usd_cost": "0.0003"})

    sized = await sizing.size_issue(
        _issue("Do it all.", paths=("docs",), checks=("make test",)), httpx.MockTransport(handler)
    )

    config = sizing.settings()
    assert (sized.size, sized.split, sized.usd_cost) == ("large", ["One", "Two"], "0.0003")
    assert sized.model == f"{config['engine']}:{config['model']}"
    assert (seen[0]["engine"], seen[0]["model"]) == (config["engine"], config["model"])
    assert "--- issue ---" in seen[0]["prompt"] and "Do it all." in seen[0]["prompt"]


async def test_sizing_says_why_it_could_not_size(monkeypatch):
    monkeypatch.delenv("GATEWAY_URL", raising=False)
    with pytest.raises(sizing.SizingError, match="GATEWAY_URL"):
        await sizing.size_issue(_issue())
    monkeypatch.setattr(sizing, "settings", lambda: {"engine": "none"})
    assert await sizing.size_issue(_issue()) is None, "engine none turns sizing off"


# The sweep, with a sizer that answers what each test sets and calls no model.


class FakeSizer:
    def __init__(self):
        self.answers: dict[str, dict] = {}
        self.calls: list[str] = []
        self.fail = False
        # What an operator does while the sweep waits on the model.
        self.meanwhile = None

    async def __call__(self, issue: Issue):
        self.calls.append(issue.ref)
        if self.meanwhile:
            self.meanwhile(issue)
        if self.fail:
            raise sizing.SizingError("the HTTP Gateway is unreachable")
        answer = self.answers.get(issue.ref, {"size": "small", "reasons": [], "missing": [], "split": []})
        return sizing.parse(answer, issue, "openai-decisions:gpt-6-luna", "0.0002")


@pytest.fixture
def world(monkeypatch):
    monkeypatch.delenv("MH_CONFIG_SOURCE", raising=False)
    events, tracker, tasks, sizer = Events(), WritingTracker(), FakeTasks(), FakeSizer()
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


def _goal(client, **fields) -> dict:
    body = {"statement": "Fix bugs", "repos": [REPO], "label": _label(), **fields}
    created = client.post("/v1/goals", json=body, headers=OPERATOR)
    assert created.status_code == 201, created.text
    return created.json()


def _sweep(client, goal: dict) -> dict:
    swept = client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR)
    assert swept.status_code == 200, swept.text
    assert swept.json()["errors"] == [], swept.text
    return swept.json()


def _shown(client, goal: dict) -> dict[str, dict]:
    return {i["ref"]: i for i in client.get(f"/v1/goals/{goal['id']}/issues", headers=OPERATOR).json()["issues"]}


def _task(tasks, ref: str) -> dict | None:
    return next((t for t in tasks.tasks.values() if t["external_ref"] == ref), None)


def _payloads(events, event_type: str) -> list[dict]:
    return [p for t, p in events.events if t == event_type]


def test_a_large_issue_is_held_with_its_split_until_an_operator_runs_it_anyway(world):
    client, events, tracker, tasks, sizer = world
    goal = _goal(client)
    ref = tracker.add(REPO, _number(), body=f"Everything.\n\nPaths: console\n\n{CHECKS}", labels=[goal["label"]])
    sizer.answers[ref] = {"size": "large", "reasons": ["22 files"], "missing": [], "split": ["Titles", "Labels"]}

    swept = _sweep(client, goal)

    assert swept["held"] == [ref] and _task(tasks, ref) is None
    [sized] = _payloads(events, "issue_sized")
    assert (sized["ref"], sized["size"], sized["split"], sized["model"]) == (
        ref,
        "large",
        ["Titles", "Labels"],
        "openai-decisions:gpt-6-luna",
    )
    card = _shown(client, goal)[ref]
    assert card["size"] == "large" and card["split"] == ["Titles", "Labels"] and card["paths"] == ["console"]
    assert "Suggested split: Titles; Labels" in card["held_reason"]

    _sweep(client, goal)
    assert sizer.calls == [ref], "an issue is sized once"
    assert _task(tasks, ref) is None, "it stays held"

    ran = client.post(f"/v1/goals/{goal['id']}/issues/run-anyway", json={"ref": ref}, headers=OPERATOR)
    assert ran.status_code == 200, ran.text
    _sweep(client, goal)

    task = _task(tasks, ref)
    assert task is not None and task["max_usd"] == "20.00", "the task policy's limit for a large issue"
    assert task["complexity"] == "high", "the size is stored on the task"
    assert _shown(client, goal)[ref]["held_reason"] is None
    assert _payloads(events, "issue_released")[0]["ref"] == ref
    assert _payloads(events, "issue_joined")[-1]["size"] == "large"
    again = client.post(f"/v1/goals/{goal['id']}/issues/run-anyway", json={"ref": ref}, headers=OPERATOR)
    assert again.status_code == 409


def test_an_issue_with_a_gap_runs_with_the_gap_shown_and_its_size_sets_the_spend_limit(world):
    client, events, tracker, tasks, sizer = world
    goal = _goal(client)
    gap = tracker.add(REPO, _number(), body=f"Fix it.\n\n{CHECKS}", labels=[goal["label"]])
    sizer.answers[gap] = {"size": "medium", "reasons": [], "missing": [], "split": []}
    own = tracker.add(
        REPO, _number(), body=f"Fix it.\n\nPaths: docs\nSpend limit: $1.50\n\n{CHECKS}", labels=[goal["label"]]
    )

    _sweep(client, goal)

    assert _task(tasks, gap)["max_usd"] == "8.00", "the task policy's limit for a medium issue"
    assert (_task(tasks, gap)["complexity"], _task(tasks, own)["complexity"]) == ("medium", "low")
    assert _task(tasks, own)["max_usd"] == "1.50", "the issue's own limit comes first"
    assert _shown(client, goal)[gap]["missing"] == ["paths"]
    assert _shown(client, goal)[own]["missing"] == []


def test_a_strict_goal_holds_an_issue_with_a_gap_until_the_issue_is_edited_or_the_goal_relents(world):
    client, events, tracker, tasks, sizer = world
    goal = _goal(client, strict_issues=True)
    assert goal["strict_issues"] is True
    ref = tracker.add(REPO, _number(), body=f"Fix it.\n\n{CHECKS}", labels=[goal["label"]])

    assert _sweep(client, goal)["held"] == [ref]
    assert "the goal is strict" in _shown(client, goal)[ref]["held_reason"]

    # Edited, with its paths: sized again, and it runs.
    tracker.issues[ref].body += "\n\nPaths: docs"
    tracker.issues[ref].paths = ["docs"]
    tracker.issues[ref].updated_at = "2026-11-12T10:00:00Z"
    _sweep(client, goal)
    assert sizer.calls == [ref, ref]
    assert _task(tasks, ref) is not None and _shown(client, goal)[ref]["held_reason"] is None

    other = tracker.add(REPO, _number(), body=f"Fix that.\n\n{CHECKS}", labels=[goal["label"]])
    assert _sweep(client, goal)["held"] == [other]
    patched = client.patch(f"/v1/goals/{goal['id']}", json={"strict_issues": False}, headers=OPERATOR)
    assert patched.status_code == 200 and patched.json()["strict_issues"] is False
    _sweep(client, goal)
    assert _task(tasks, other) is not None


def test_an_issue_the_sizer_cannot_size_runs_unsized(world):
    client, events, tracker, tasks, sizer = world
    sizer.fail = True
    goal = _goal(client)
    ref = tracker.add(REPO, _number(), body=f"Fix it.\n\n{CHECKS}", labels=[goal["label"]])

    _sweep(client, goal)

    assert _task(tasks, ref) is not None and "max_usd" not in _task(tasks, ref)
    assert "complexity" not in _task(tasks, ref)
    assert _shown(client, goal)[ref]["size"] is None
    assert _payloads(events, "issue_sized") == []


def test_split_creates_the_suggested_issues_with_the_goals_label_and_their_paths(world):
    client, events, tracker, tasks, sizer = world
    goal = _goal(client)
    ref = tracker.add(REPO, _number(), body=f"Everything.\n\n{CHECKS}", labels=[goal["label"]])
    sizer.answers[ref] = {"size": "large", "reasons": [], "missing": [], "split": ["Titles", "Labels"]}
    _sweep(client, goal)
    url = f"/v1/goals/{goal['id']}/issues/split"

    pathless = client.post(
        url, json={"ref": ref, "issues": [{"title": "Titles", "body": "No paths."}]}, headers=OPERATOR
    )
    assert pathless.status_code == 422 and "Titles" in pathless.json()["detail"]
    assert tracker.created == []

    drafts = [{"title": t, "body": f"Split from {ref}.\n\nPaths: console\n\n{CHECKS}"} for t in ("Titles", "Labels")]
    split = client.post(url, json={"ref": ref, "issues": drafts}, headers=OPERATOR)

    assert split.status_code == 201, split.text
    created = split.json()["created"]
    assert [i.title for i in tracker.created] == ["Titles", "Labels"]
    assert all(i.labels == [goal["label"]] for i in tracker.created)
    assert (REPO, goal["label"]) in tracker.labels
    assert _payloads(events, "issue_split") == [{"ref": ref, "goal_id": goal["id"], "issues": created}]
    reason = _shown(client, goal)[ref]["held_reason"]
    assert reason.startswith("split into #")
    assert _shown(client, goal)[ref]["split_into"] == created

    # The new issues carry the label, so the next sweep sizes them and takes them in; the original stays held,
    # its split kept, even once it is edited.
    for issue in tracker.created:
        issue.paths, issue.checks = ["console"], ["make test"]
    tracker.issues[ref].updated_at = "2026-11-12T10:00:00Z"
    swept = _sweep(client, goal)
    assert all(_task(tasks, r) is not None for r in created) and _task(tasks, ref) is None
    assert ref in swept["held"]
    card = _shown(client, goal)[ref]
    assert (card["held_reason"], card["split_into"]) == (reason, created), "the split outlives the sweep"
    assert sizer.calls.count(ref) == 1, "a split issue is not sized again"

    again = client.post(url, json={"ref": ref, "issues": drafts}, headers=OPERATOR)
    assert again.status_code == 409 and "split already" in again.json()["detail"]
    assert len(tracker.created) == 2, "no issue is created twice"
    _sweep(client, goal)
    assert _shown(client, goal)[ref]["held_reason"] == reason

    # Run anyway is still open to it.
    ran = client.post(f"/v1/goals/{goal['id']}/issues/run-anyway", json={"ref": ref}, headers=OPERATOR)
    assert ran.status_code == 200, ran.text
    _sweep(client, goal)
    assert _task(tasks, ref) is not None


@pytest.fixture
def no_default_checks(tmp_path, monkeypatch):
    """A repo registry that names no default checks for the repo."""
    from meta_harness.planner import repos

    checkless = tmp_path / "repos.json"
    checkless.write_text(json.dumps({"version": "1", "repos": {REPO: {"tracker": "github"}}}))
    monkeypatch.setenv("MH_REPOS_CONFIG", str(checkless))
    repos._file_registry.cache_clear()
    yield
    repos._file_registry.cache_clear()


def test_an_issue_with_no_checks_anywhere_is_sized_and_runs_with_the_gap_shown(world, no_default_checks):
    client, events, tracker, tasks, sizer = world
    goal = _goal(client)
    ref = tracker.add(REPO, _number(), body="Fix it.\n\nPaths: docs", labels=[goal["label"]])

    swept = _sweep(client, goal)

    assert swept["refused"] == [] and swept["joined"] == [ref]
    assert [p["ref"] for p in _payloads(events, "issue_sized")] == [ref]
    assert _task(tasks, ref)["acceptance_checks"] == [], "it runs with no checks"
    card = _shown(client, goal)[ref]
    assert card["missing"] == ["acceptance_checks"] and card["refused_reason"] is None


def test_a_strict_goal_holds_an_issue_with_no_checks_anywhere_until_it_is_run_anyway(world, no_default_checks):
    client, events, tracker, tasks, sizer = world
    goal = _goal(client, strict_issues=True)
    ref = tracker.add(REPO, _number(), body="Fix it.\n\nPaths: docs", labels=[goal["label"]])

    swept = _sweep(client, goal)

    assert swept["held"] == [ref] and swept["refused"] == [] and _task(tasks, ref) is None
    assert [p["ref"] for p in _payloads(events, "issue_sized")] == [ref]
    card = _shown(client, goal)[ref]
    assert "missing acceptance checks" in card["held_reason"] and card["refused_reason"] is None

    ran = client.post(f"/v1/goals/{goal['id']}/issues/run-anyway", json={"ref": ref}, headers=OPERATOR)
    assert ran.status_code == 200, ran.text
    _sweep(client, goal)
    assert _task(tasks, ref)["acceptance_checks"] == []


def test_an_unsized_issue_with_no_checks_anywhere_is_refused_as_before_sizing(world, no_default_checks):
    client, events, tracker, tasks, sizer = world
    sizer.fail = True
    goal = _goal(client)
    ref = tracker.add(REPO, _number(), body="Fix it.", labels=[goal["label"]])

    assert _sweep(client, goal)["refused"] == [ref]
    assert _task(tasks, ref) is None and "no ## Acceptance checks" in _shown(client, goal)[ref]["refused_reason"]


def test_only_a_held_issue_of_the_goal_is_run_anyway_or_split(world):
    client, events, tracker, tasks, sizer = world
    goal = _goal(client)
    ref = tracker.add(REPO, _number(), body=f"Fix it.\n\nPaths: docs\n\n{CHECKS}", labels=[goal["label"]])
    _sweep(client, goal)

    running = client.post(f"/v1/goals/{goal['id']}/issues/run-anyway", json={"ref": ref}, headers=OPERATOR)
    assert running.status_code == 409
    stranger = f"github:{REPO}#{uuid.uuid4().int % 10**9 + 10**12}"
    missing = client.post(f"/v1/goals/{goal['id']}/issues/run-anyway", json={"ref": stranger}, headers=OPERATOR)
    assert missing.status_code == 404


def test_run_anyway_waits_while_the_issue_is_being_split(world):
    from meta_harness import db
    from meta_harness.planner import store

    client, events, tracker, tasks, sizer = world
    goal = _goal(client)
    ref = tracker.add(REPO, _number(), body=f"Everything.\n\nPaths: docs\n\n{CHECKS}", labels=[goal["label"]])
    sizer.answers[ref] = {"size": "large", "reasons": [], "missing": [], "split": ["Titles", "Labels"]}
    _sweep(client, goal)
    url = f"/v1/goals/{goal['id']}/issues/run-anyway"

    # As the split route does before it asks GitHub for the smaller issues.
    with db.connect(autocommit=True) as conn:
        assert store.claim_split(conn, goal["id"], ref)
    splitting = client.post(url, json={"ref": ref}, headers=OPERATOR)
    assert splitting.status_code == 409 and "being split" in splitting.json()["detail"]
    _sweep(client, goal)
    assert _task(tasks, ref) is None, "the original does not start while it is split"

    # The split ends with nothing created: its claim is let go, and Run anyway is open again.
    with db.connect(autocommit=True) as conn:
        store.record_split(conn, ref, [], None)
    ran = client.post(url, json={"ref": ref}, headers=OPERATOR)
    assert ran.status_code == 200, ran.text
    _sweep(client, goal)
    assert _task(tasks, ref) is not None


def test_an_issue_split_while_the_sweep_sizes_it_makes_no_task_and_keeps_its_split(world):
    from meta_harness import db
    from meta_harness.planner import store

    client, events, tracker, tasks, sizer = world
    goal = _goal(client)
    ref = tracker.add(REPO, _number(), body=f"Everything.\n\nPaths: docs\n\n{CHECKS}", labels=[goal["label"]])
    sizer.answers[ref] = {"size": "large", "reasons": [], "missing": [], "split": ["Titles", "Labels"]}
    assert _sweep(client, goal)["held"] == [ref]

    # Edited, so the next sweep sizes it again, small now; while it waits on the model, an operator splits it.
    tracker.issues[ref].updated_at = "2026-11-12T10:00:00Z"
    sizer.answers[ref] = {"size": "small", "reasons": [], "missing": [], "split": []}
    created = [f"github:{REPO}#{_number()}", f"github:{REPO}#{_number()}"]
    reason = sizing.split_reason(created)

    def split(issue: Issue) -> None:
        with db.connect(autocommit=True) as conn:
            assert store.claim_split(conn, goal["id"], issue.ref)
            store.record_split(conn, issue.ref, created, reason)

    sizer.meanwhile = split
    swept = _sweep(client, goal)

    assert sizer.calls == [ref, ref]
    assert ref in swept["held"] and _task(tasks, ref) is None, "the original makes no task beside its split"
    card = _shown(client, goal)[ref]
    assert (card["held_reason"], card["split_into"]) == (reason, created), "the split's reason is kept"
