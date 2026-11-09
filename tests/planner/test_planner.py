import uuid

import pytest
from fastapi.testclient import TestClient

from meta_harness import db, migrations
from meta_harness.planner import tracker as tracker_module
from meta_harness.planner.service import create_app
from meta_harness.planner.tracker import Issue, depends_on
from meta_harness.tasks.client import TaskApiError

pytestmark = pytest.mark.db
OPERATOR = {"Authorization": "Bearer op-key"}


@pytest.fixture(scope="module", autouse=True)
def migrated():
    try:
        with db.connect() as conn:
            migrations.apply(conn)
    except Exception as err:  # noqa: BLE001
        pytest.skip(f"no Postgres at {db.database_url()}: {err}")


class Events:
    def __init__(self):
        self.events = []

    def emit(self, event_type, payload=None, **fields):
        self.events.append((event_type, payload or {}))
        return "id"

    def types(self):
        return [t for t, _ in self.events]


class FakeTracker:
    def __init__(self):
        self.issues: dict[str, Issue] = {}

    def add(self, repo, number, body="", labels=("mh:test",), state="open", title="Fix it"):
        ref = tracker_module.ref_for(repo, number)
        self.issues[ref] = Issue(
            ref=ref,
            repo=repo,
            number=number,
            title=title,
            body=body,
            labels=list(labels),
            state=state,
            depends_on=depends_on(repo, body),
            checks=tracker_module.acceptance_checks(body),
        )
        return ref

    async def list_open(self, repo, label):
        return [
            i
            for i in self.issues.values()
            if i.repo == repo and i.state == "open" and (label is None or label in i.labels)
        ]

    async def read(self, ref):
        return self.issues[ref]


class FakeTasks:
    def __init__(self):
        self.tasks: dict[str, dict] = {}

    def create(self, **task):
        for existing in self.tasks.values():
            if existing["external_ref"] == task["external_ref"]:
                return existing
        task_id = f"t-{uuid.uuid4().hex[:6]}"
        self.tasks[task_id] = {**task, "id": task_id, "status": "blocked" if task.get("depends_on") else "queued"}
        return self.tasks[task_id]

    def get(self, task_id):
        if task_id not in self.tasks:
            raise TaskApiError(404, "no such task")
        return self.tasks[task_id]

    def set_status(self, task_id, status, reason=None, **fields):
        self.tasks[task_id]["status"] = status
        return self.tasks[task_id]

    def list(self, statuses=None, goal_id=None, limit=100):
        found = [
            t
            for t in self.tasks.values()
            if (not statuses or t["status"] in statuses) and (not goal_id or t.get("goal_id") == goal_id)
        ]
        return found[:limit]


@pytest.fixture
def parts():
    return Events(), FakeTracker(), FakeTasks()


@pytest.fixture
def api(parts):
    events, tracker, tasks = parts
    app = create_app(events, tracker, tasks, operator_keys={"op-key": "dave"}, service_keys={}, sweep_seconds=0)
    with TestClient(app) as client:
        yield client


def _label() -> str:
    return f"mh:test-{uuid.uuid4().hex[:6]}"


def test_depends_on_reads_same_repo_and_other_repo_refs():
    body = "Fix it.\n\nDepends on: #12, davecthomas/ai-api-unified#7\n"
    assert depends_on("davecthomas/meta-harness", body) == [
        "github:davecthomas/meta-harness#12",
        "github:davecthomas/ai-api-unified#7",
    ]


def test_a_goal_needs_registered_repos_and_a_built_modality(api):
    base = {"statement": "Fix bugs", "label": _label()}
    assert api.post("/v1/goals", json={**base, "repos": ["someone/else"]}, headers=OPERATOR).status_code == 422
    feature = {**base, "repos": ["davecthomas/meta-harness"], "modality": "feature"}
    assert api.post("/v1/goals", json=feature, headers=OPERATOR).status_code == 422
    assert api.post("/v1/goals", json={**base, "repos": ["davecthomas/meta-harness"]}).status_code == 401


async def test_a_sweep_turns_labeled_issues_into_tasks_once(api, parts):
    events, tracker, tasks = parts
    label = _label()
    goal = api.post(
        "/v1/goals",
        json={"statement": "Fix bugs", "repos": ["davecthomas/meta-harness"], "label": label},
        headers=OPERATOR,
    ).json()
    with_checks = tracker.add(
        "davecthomas/meta-harness",
        9001 + hash(label) % 1000,
        body="Broken.\n\n## Acceptance checks\n- `make test`\n",
        labels=[label],
    )
    defaults = tracker.add("davecthomas/meta-harness", 8001 + hash(label) % 1000, body="Also broken.", labels=[label])
    _sweep(api, goal)
    again = _sweep(api, goal)
    assert again["joined"] == []
    assert len(tasks.tasks) == 2
    by_ref = {t["external_ref"]: t for t in tasks.tasks.values()}
    assert by_ref[with_checks]["acceptance_checks"] == ["make test"]
    assert by_ref[defaults]["acceptance_checks"] == ["uv sync", "uv run ruff check .", "uv run pytest -q"]
    assert by_ref[with_checks]["goal_id"] == goal["id"]
    shown = api.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()
    assert {i["ref"] for i in shown["issues"]} == {with_checks, defaults}


async def test_a_task_waits_on_an_open_dependency_and_is_released_when_it_closes(api, parts):
    events, tracker, tasks = parts
    label = _label()
    goal = api.post(
        "/v1/goals",
        json={"statement": "Fix bugs", "repos": ["davecthomas/meta-harness"], "label": label},
        headers=OPERATOR,
    ).json()
    n = 7000 + hash(label) % 1000
    first = tracker.add("davecthomas/meta-harness", n, labels=[label])
    second = tracker.add("davecthomas/meta-harness", n + 1, body=f"Depends on: #{n}", labels=[label])
    _sweep(api, goal)
    by_ref = {t["external_ref"]: t for t in tasks.tasks.values()}
    assert by_ref[second]["status"] == "blocked"
    tracker.issues[first].state = "closed"
    _sweep(api, goal)
    assert by_ref[second]["status"] == "queued"
    issues = {i["ref"]: i for i in api.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["issues"]}
    assert issues[first]["state"] == "closed"
    assert events.types().count("task_unblocked") == 1


def _sweep(api, goal) -> dict:
    response = api.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR)
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_paused_goal_is_not_swept_now(api):
    goal = api.post(
        "/v1/goals",
        json={"statement": "Fix bugs", "repos": ["davecthomas/meta-harness"], "label": _label()},
        headers=OPERATOR,
    ).json()
    assert api.post(f"/v1/goals/{goal['id']}/pause", headers=OPERATOR).json()["status"] == "paused"
    assert api.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR).status_code == 409
    assert api.post(f"/v1/goals/{goal['id']}/resume", headers=OPERATOR).json()["status"] == "active"


# --- A goal's end: met on its own, completed by hand, reopened ---------------------------------------


def _goal(api, label) -> dict:
    response = api.post(
        "/v1/goals",
        json={"statement": "Fix bugs", "repos": ["davecthomas/meta-harness"], "label": label},
        headers=OPERATOR,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_a_goal_is_met_when_its_last_issue_closes_and_its_tasks_are_done(api, parts):
    events, tracker, tasks = parts
    label = _label()
    goal = _goal(api, label)
    assert _sweep(api, goal)["met"] is False, "a goal that never held an issue waits for its first one"
    ref = tracker.add("davecthomas/meta-harness", _number(), labels=[label])
    _sweep(api, goal)
    [task] = [t for t in tasks.tasks.values() if t["external_ref"] == ref]
    tracker.issues[ref].state = "closed"
    task["status"] = "running"
    assert _sweep(api, goal)["met"] is False, "a running task keeps the goal open"
    assert api.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["status"] == "active"
    task["status"] = "closed"

    assert _sweep(api, goal)["met"] is True

    shown = api.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()
    assert shown["status"] == "met" and shown["closed_at"]
    met = [p for t, p in events.events if t == "goal_met"]
    assert met[-1]["goal_id"] == goal["id"]
    changed = [p for t, p in events.events if t == "goal_changed" and p["goal_id"] == goal["id"]]
    assert changed[-1]["changes"] == {"status": {"from": "active", "to": "met"}}


async def test_a_goal_whose_label_picks_up_a_new_issue_stays_active(api, parts):
    _, tracker, tasks = parts
    label = _label()
    goal = _goal(api, label)
    first = tracker.add("davecthomas/meta-harness", _number(), labels=[label])
    _sweep(api, goal)
    tracker.issues[first].state = "closed"
    for t in tasks.tasks.values():
        t["status"] = "closed"
    second = tracker.add("davecthomas/meta-harness", _number(), labels=[label])

    result = _sweep(api, goal)

    assert result["met"] is False and second in result["joined"]
    assert api.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["status"] == "active"


async def test_a_reopened_goal_takes_in_its_newly_opened_issue(api, parts):
    events, tracker, tasks = parts
    label = _label()
    goal = _goal(api, label)
    first = tracker.add("davecthomas/meta-harness", _number(), labels=[label])
    _sweep(api, goal)
    tracker.issues[first].state = "closed"
    for t in tasks.tasks.values():
        t["status"] = "closed"
    assert _sweep(api, goal)["met"] is True
    assert api.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR).status_code == 409, "a met goal takes nothing"
    second = tracker.add("davecthomas/meta-harness", _number(), labels=[label])
    assert api.post(f"/v1/goals/{goal['id']}/resume", headers=OPERATOR).status_code == 409, "resume is not reopen"

    reopened = api.post(f"/v1/goals/{goal['id']}/reopen", headers=OPERATOR)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "active" and reopened.json()["closed_at"] is None
    _sweep(api, goal)

    assert any(t["external_ref"] == second and t["goal_id"] == goal["id"] for t in tasks.tasks.values())
    shown = api.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()
    assert shown["status"] == "active" and second in {i["ref"] for i in shown["issues"]}
    moves = [p["changes"]["status"] for t, p in events.events if t == "goal_changed" and p["goal_id"] == goal["id"]]
    assert moves == [{"from": "active", "to": "met"}, {"from": "met", "to": "active"}]


def test_an_operator_completes_a_goal_and_reopens_a_canceled_one(api, parts):
    events, _, _ = parts
    goal = _goal(api, _label())
    assert api.post(f"/v1/goals/{goal['id']}/pause", headers=OPERATOR).json()["status"] == "paused"

    completed = api.post(f"/v1/goals/{goal['id']}/complete", headers=OPERATOR).json()

    assert completed["status"] == "met" and completed["closed_at"]
    assert [p for t, p in events.events if t == "goal_met"][-1] == {
        "goal_id": goal["id"],
        "reason": "completed by dave",
    }
    assert api.post(f"/v1/goals/{goal['id']}/reopen", headers=OPERATOR).json()["status"] == "active"
    assert api.post(f"/v1/goals/{goal['id']}/cancel", headers=OPERATOR).json()["status"] == "canceled"
    assert api.post(f"/v1/goals/{goal['id']}/complete", headers=OPERATOR).status_code == 409
    assert api.post(f"/v1/goals/{goal['id']}/reopen", headers=OPERATOR).json()["status"] == "active"


def test_a_goal_saved_without_starting_is_not_checked(api, parts, conn):
    from meta_harness.planner import store

    goal = _goal(api, _label())
    store.update(conn, goal["id"], status="paused")
    assert store.update(conn, goal["id"], only_if="active", status="met")[1] == {}
    assert store.get(conn, goal["id"])["status"] == "paused"


# --- Goals from labels --------------------------------------------------------------------------------

REPO = "davecthomas/meta-harness"


def _number() -> int:
    """An issue number no other test uses: the other tests take numbers under 11000, and links persist."""
    return 100_000 + uuid.uuid4().int % 900_000


def test_labels_are_listed_with_their_open_issue_counts(api, parts):
    _, tracker, _ = parts
    label = _label()
    tracker.add(REPO, _number(), labels=[label, "bug"])
    tracker.add(REPO, _number(), labels=[label])
    tracker.add(REPO, _number(), labels=[label], state="closed")

    found = api.get("/v1/labels", params={"repo": REPO}, headers=OPERATOR).json()

    row = next(r for r in found["labels"] if r["label"] == label)
    assert row == {"label": label, "total": 2, "repos": {REPO: 2}, "held": 0, "held_by": []}
    assert api.get("/v1/labels", params={"repo": "someone/else"}, headers=OPERATOR).status_code == 404
    assert api.get("/v1/labels").status_code == 401


async def test_a_labels_issues_show_which_open_goal_holds_each(api, parts):
    _, tracker, _ = parts
    label = _label()
    held = tracker.add(REPO, _number(), labels=[label])
    goal = api.post("/v1/goals", json={"statement": "Fix", "repos": [REPO], "label": label}, headers=OPERATOR).json()
    _sweep(api, goal)
    free = tracker.add(REPO, _number(), labels=[label], title="Not held yet")

    listed = api.get(f"/v1/repos/{REPO}/issues", params={"label": label}, headers=OPERATOR).json()

    by_ref = {i["ref"]: i for i in listed["issues"]}
    assert by_ref[held]["held_by"] == goal["id"]
    assert by_ref[free]["held_by"] is None and by_ref[free]["title"] == "Not held yet"
    api.post(f"/v1/goals/{goal['id']}/cancel", headers=OPERATOR)
    again = api.get(f"/v1/repos/{REPO}/issues", params={"label": label}, headers=OPERATOR).json()
    assert {i["ref"]: i["held_by"] for i in again["issues"]}[held] is None, "a canceled goal holds nothing"


def _drafting_api(parts, drafter):
    events, tracker, tasks = parts
    app = create_app(
        events, tracker, tasks, operator_keys={"op-key": "dave"}, service_keys={}, sweep_seconds=0, drafter=drafter
    )
    return TestClient(app)


def test_a_goal_is_drafted_from_the_chosen_issues_and_nothing_is_saved(parts):
    from meta_harness.planner.drafting import Draft

    events, tracker, _ = parts
    label = _label()
    first = tracker.add(REPO, _number(), labels=[label], title="First")
    tracker.add(REPO, _number(), labels=[label], title="Left out")
    seen = []

    async def drafter(label_asked, issues):
        seen.append((label_asked, [i.ref for i in issues]))
        return Draft(title="T", statement="S", theme="Th", modality="feature", model="claude:m", usd_cost="0.001")

    with _drafting_api(parts, drafter) as client:
        drafted = client.post(
            "/v1/goals/draft", json={"label": label, "repos": [REPO], "refs": [first]}, headers=OPERATOR
        )
        goals_after = client.get("/v1/goals", headers=OPERATOR).json()["goals"]

    assert drafted.status_code == 200
    body = drafted.json()
    assert (body["title"], body["statement"], body["modality"], body["theme"]) == ("T", "S", "feature", "Th")
    assert body["refs"] == [first] and body["label"] == label and body["repos"] == [REPO]
    assert seen == [(label, [first])]
    assert not any(g["label"] == label for g in goals_after), "a draft creates no goal"
    drafted_events = [p for t, p in events.events if t == "goal_drafted"]
    assert drafted_events[-1] == {
        "label": label,
        "repos": [REPO],
        "issues": 1,
        "model": "claude:m",
        "usd_cost": "0.001",
    }


def test_a_draft_the_gateway_could_not_make_is_a_502_with_the_reason(parts):
    from meta_harness.planner.drafting import DraftError

    async def drafter(label, issues):
        raise DraftError("the model did not finish its draft (max_tokens)")

    with _drafting_api(parts, drafter) as client:
        refused = client.post("/v1/goals/draft", json={"label": _label(), "repos": [REPO]}, headers=OPERATOR)

    assert refused.status_code == 502 and "did not finish" in refused.json()["detail"]
