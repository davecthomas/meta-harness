import asyncio
import copy
import dataclasses
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
        self.stored: dict[str, dict] = {}

    def emit(self, event_type, payload=None, **fields):
        self.events.append((event_type, payload or {}))
        event_id = fields.get("event_id") or uuid.uuid4().hex
        if event_id not in self.stored:
            position = len(self.stored) + 1
            event = {"position": position, "event_type": event_type, "payload": payload or {}, **fields}
            self.stored[event_id] = event
        return event_id

    def read(self, from_position=0, limit=500, before_position=None, **filters):
        def wanted(event):
            return all(str(event.get(k)) in v.split(",") for k, v in filters.items())

        found = [e for e in self.stored.values() if e["position"] > from_position and wanted(e)][:limit]
        return {"events": found, "next_position": found[-1]["position"] if found else from_position}

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


async def _no_title(statement: str):
    """A titler that fails, as with no HTTP Gateway: a test never calls a model."""
    from meta_harness.planner.drafting import DraftError

    raise DraftError("no model in tests")


@pytest.fixture
def api(parts):
    events, tracker, tasks = parts
    app = create_app(
        events, tracker, tasks, operator_keys={"op-key": "dave"}, service_keys={}, sweep_seconds=0, titler=_no_title
    )
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
    # A fresh number: issue links persist across runs, so a reused one may belong to an older goal.
    n = _number()
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


async def test_a_created_goal_starts_at_once_by_default(api, parts):
    events, _, _ = parts
    goal = api.post(
        "/v1/goals",
        json={"statement": "Fix bugs", "repos": ["davecthomas/meta-harness"], "label": _label()},
        headers=OPERATOR,
    ).json()
    assert goal["status"] == "active"
    assert goal["started_at"] is not None
    [created] = [p for t, p in events.events if t == "goal_created"]
    assert created["start"] is True


async def test_a_goal_saved_without_starting_takes_in_nothing_until_it_starts(api, parts):
    events, tracker, tasks = parts
    label = _label()
    goal = api.post(
        "/v1/goals",
        json={"statement": "Fix bugs", "repos": ["davecthomas/meta-harness"], "label": label, "start": False},
        headers=OPERATOR,
    ).json()
    assert goal["status"] == "paused"
    assert goal["started_at"] is None
    [created] = [p for t, p in events.events if t == "goal_created"]
    assert created["start"] is False
    tracker.add("davecthomas/meta-harness", _number(), body="Broken.", labels=[label])
    assert api.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR).status_code == 409
    shown = api.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()
    assert shown["started_at"] is None
    assert shown["issues"] == []
    assert tasks.tasks == {}

    started = api.post(f"/v1/goals/{goal['id']}/resume", headers=OPERATOR).json()
    assert started["status"] == "active"
    assert started["started_at"] is not None
    [changed] = [p for t, p in events.events if t == "goal_changed"]
    assert changed["changes"]["status"] == {"from": "paused", "to": "active"}
    assert "started_at" in changed["changes"]
    # The first start sweeps at once, as creating an active goal does.
    for _ in range(50):
        if tasks.tasks:
            break
        await asyncio.sleep(0.05)
    assert len(tasks.tasks) == 1

    api.post(f"/v1/goals/{goal['id']}/pause", headers=OPERATOR)
    again = api.post(f"/v1/goals/{goal['id']}/resume", headers=OPERATOR).json()
    assert again["started_at"] == started["started_at"]


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


class MergedHost:
    """GitHub, where every pull request has merged."""

    async def read_pull_request(self, repo, number):
        from meta_harness.run_service.code_host import PullRequest

        return PullRequest(repo, number, f"https://github.com/{repo}/pull/{number}", "merged", "h1", "m1")

    async def check_runs(self, repo, sha):
        return []


async def test_a_ready_tasks_merged_pull_request_closes_it_and_shows_on_the_goals_issues(parts):
    events, tracker, tasks = parts
    app = create_app(
        events, tracker, tasks, operator_keys={"op-key": "dave"}, service_keys={}, sweep_seconds=0, host=MergedHost()
    )
    with TestClient(app) as client:
        label = _label()
        goal = _goal(client, label)
        ref = tracker.add(REPO, _number(), labels=[label])
        _sweep(client, goal)
        [task] = [t for t in tasks.tasks.values() if t["external_ref"] == ref]
        task.update(status="ready", pr_url=f"https://github.com/{REPO}/pull/7", head_sha="h1")

        assert _sweep(client, goal)["merged"] == [task["id"]]
        shown = client.get(f"/v1/goals/{goal['id']}/issues", headers=OPERATOR)

    assert task["status"] == "closed"
    assert shown.status_code == 200, shown.text
    [issue] = shown.json()["issues"]
    assert issue["ref"] == ref and issue["task_status"] == "closed"
    assert issue["pr_url"] == f"https://github.com/{REPO}/pull/7" and issue["pr_state"] == "merged"


def _drafting_api(parts, drafter):
    events, tracker, tasks = parts
    app = create_app(
        events,
        tracker,
        tasks,
        operator_keys={"op-key": "dave"},
        service_keys={},
        sweep_seconds=0,
        drafter=drafter,
        titler=_no_title,
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


# --- A goal's title -----------------------------------------------------------------------------------


def _titled_api(parts, titles: list[str]):
    """The planner with a titler that names each goal from titles, in order, and records what it read."""
    from meta_harness.planner.drafting import Title

    read: list[str] = []

    async def titler(statement: str) -> Title:
        read.append(statement)
        return Title(title=titles.pop(0), model="claude:claude-haiku-4-5", usd_cost="0.0001")

    events, tracker, tasks = parts
    app = create_app(
        events, tracker, tasks, operator_keys={"op-key": "dave"}, service_keys={}, sweep_seconds=0, titler=titler
    )
    return app, read


async def _until(check, tries: int = 50):
    for _ in range(tries):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out")


async def test_a_goal_created_with_a_title_keeps_it_and_no_model_names_it(parts):
    events, _, _ = parts
    app, read = _titled_api(parts, ["Unused"])
    with TestClient(app) as client:
        goal = client.post(
            "/v1/goals",
            json={"title": "Save bugs", "statement": "Fix the save bugs.", "repos": [REPO], "label": _label()},
            headers=OPERATOR,
        ).json()
        await asyncio.sleep(0.05)

    assert goal["title"] == "Save bugs"
    assert [p["title"] for t, p in events.events if t == "goal_created"] == ["Save bugs"]
    assert read == [] and not [p for t, p in events.events if t == "goal_titled"]


async def test_a_goal_created_without_a_title_gets_one_from_the_model(parts):
    events, _, _ = parts
    app, read = _titled_api(parts, ["Fix the save bugs"])
    with TestClient(app) as client:
        goal = client.post(
            "/v1/goals",
            json={"statement": "Every save loses the last edit. Fix it.", "repos": [REPO], "label": _label()},
            headers=OPERATOR,
        ).json()
        assert goal["title"] is None, "creating the goal never waits on the model"
        await _until(lambda: [p for t, p in events.events if t == "goal_titled"])
        shown = client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()

    assert read == ["Every save loses the last edit. Fix it."]
    assert shown["title"] == "Fix the save bugs"
    [titled] = [p for t, p in events.events if t == "goal_titled"]
    assert titled == {
        "goal_id": goal["id"],
        "title": "Fix the save bugs",
        "model": "claude:claude-haiku-4-5",
        "usd_cost": "0.0001",
    }


async def test_a_model_that_fails_leaves_the_goal_untitled(api, parts):
    events, _, _ = parts
    goal = api.post(
        "/v1/goals", json={"statement": "Fix it.", "repos": [REPO], "label": _label()}, headers=OPERATOR
    ).json()
    await asyncio.sleep(0.05)

    assert api.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["title"] is None
    assert not [p for t, p in events.events if t == "goal_titled"]


def test_an_operator_changes_a_goals_title(api, parts):
    events, _, _ = parts
    goal = api.post(
        "/v1/goals",
        json={"title": "Old", "statement": "Fix it.", "repos": [REPO], "label": _label()},
        headers=OPERATOR,
    ).json()

    changed = api.patch(f"/v1/goals/{goal['id']}", json={"title": "New name"}, headers=OPERATOR).json()

    assert changed["title"] == "New name" and changed["statement"] == "Fix it."
    moves = [p["changes"] for t, p in events.events if t == "goal_changed" and p["goal_id"] == goal["id"]]
    assert moves == [{"title": {"from": "Old", "to": "New name"}}]
    too_long = api.patch(f"/v1/goals/{goal['id']}", json={"title": "x" * 81}, headers=OPERATOR)
    assert too_long.status_code == 422


async def test_at_startup_the_planner_titles_every_goal_that_has_none(parts, conn):
    from meta_harness.planner import store

    first = store.create(conn, "dave", store.NewGoal(statement="First goal.", repos=[REPO], label=_label()))
    second = store.create(conn, "dave", store.NewGoal(statement="Second goal.", repos=[REPO], label=_label()))
    untitled = [g["id"] for g in store.untitled(conn)]
    app, read = _titled_api(parts, [f"Title {n}" for n in range(len(untitled))])
    with TestClient(app):
        await app.state.title_untitled()

    assert store.get(conn, first["id"])["title"] and store.get(conn, second["id"])["title"]
    assert store.untitled(conn) == []
    assert read[untitled.index(first["id"])] == "First goal."


# --- Issues picked by ref -------------------------------------------------------------------------------


def test_a_goal_made_from_picked_issues_with_no_label_takes_in_only_those(api, parts):
    events, tracker, tasks = parts
    picked = tracker.add(REPO, _number(), title="Picked one")
    other = tracker.add(REPO, _number(), labels=[], title="Not picked")
    created = api.post(
        "/v1/goals",
        json={"statement": "Fix the picked issue.", "repos": [REPO], "refs": [picked], "start": False},
        headers=OPERATOR,
    )
    assert created.status_code == 201, created.text
    goal = created.json()
    assert goal["label"] is None
    assert [p for t, p in events.events if t == "issue_added"] == [{"ref": picked, "goal_id": goal["id"]}]

    api.post(f"/v1/goals/{goal['id']}/resume", headers=OPERATOR)
    _sweep(api, goal)

    assert [p["ref"] for t, p in events.events if t == "issue_joined" and p["goal_id"] == goal["id"]] == [picked]
    assert {t["external_ref"] for t in tasks.tasks.values() if t["goal_id"] == goal["id"]} == {picked}
    assert other not in {t["external_ref"] for t in tasks.tasks.values()}


def test_a_goal_needs_a_label_or_picked_issues(api):
    refused = api.post("/v1/goals", json={"statement": "Nothing.", "repos": [REPO]}, headers=OPERATOR)
    assert refused.status_code == 422 and "label" in refused.text


def test_issues_are_added_by_ref_and_one_another_goal_holds_is_refused_naming_it(api, parts):
    _, tracker, _ = parts
    first = api.post(
        "/v1/goals", json={"statement": "A.", "repos": [REPO], "label": _label(), "start": False}, headers=OPERATOR
    ).json()
    second = api.post(
        "/v1/goals", json={"statement": "B.", "repos": [REPO], "label": _label(), "start": False}, headers=OPERATOR
    ).json()
    ref = tracker.add(REPO, _number())

    added = api.post(f"/v1/goals/{first['id']}/issues", json={"refs": [ref, ref]}, headers=OPERATOR)
    assert added.json()["added"] == [ref]
    assert api.post(f"/v1/goals/{first['id']}/issues", json={"refs": [ref]}, headers=OPERATOR).json()["added"] == []

    taken = api.post(f"/v1/goals/{second['id']}/issues", json={"refs": [ref]}, headers=OPERATOR)
    assert taken.status_code == 409 and first["id"] in taken.json()["detail"]
    outside = api.post(f"/v1/goals/{second['id']}/issues", json={"refs": ["github:o/elsewhere#1"]}, headers=OPERATOR)
    assert outside.status_code == 422 and "not one of the goal's repos" in outside.json()["detail"]
    malformed = api.post(f"/v1/goals/{second['id']}/issues", json={"refs": ["#12"]}, headers=OPERATOR)
    assert malformed.status_code == 422


def test_an_added_issue_with_no_task_can_be_dropped_and_one_with_a_task_cannot(api, parts):
    _, tracker, _ = parts
    goal = api.post(
        "/v1/goals", json={"statement": "A.", "repos": [REPO], "label": _label(), "start": False}, headers=OPERATOR
    ).json()
    waiting = tracker.add(REPO, _number(), labels=[])
    api.post(f"/v1/goals/{goal['id']}/issues", json={"refs": [waiting]}, headers=OPERATOR)

    dropped = api.delete(f"/v1/goals/{goal['id']}/issues/{waiting.replace('#', '%23')}", headers=OPERATOR)
    assert dropped.status_code == 200, dropped.text
    assert waiting not in {i["ref"] for i in api.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["issues"]}

    started = tracker.add(REPO, _number(), labels=[])
    api.post(f"/v1/goals/{goal['id']}/issues", json={"refs": [started]}, headers=OPERATOR)
    api.post(f"/v1/goals/{goal['id']}/resume", headers=OPERATOR)
    _sweep(api, goal)
    kept = api.delete(f"/v1/goals/{goal['id']}/issues/{started.replace('#', '%23')}", headers=OPERATOR)
    assert kept.status_code == 409 and "cancel the task" in kept.json()["detail"]


def test_a_repos_open_issues_are_browsed_by_label_text_and_page(api, parts):
    _, tracker, _ = parts
    label = _label()
    numbers = sorted(_number() for _ in range(3))
    for n in numbers:
        tracker.add(REPO, n, labels=[label], title=f"Crash number {n}")
    tracker.add(REPO, _number(), labels=[label], title="Something else")

    first = api.get(
        f"/v1/repos/{REPO}/issues", params={"label": label, "q": "crash", "limit": 2}, headers=OPERATOR
    ).json()
    assert first["total"] == 3 and first["next_cursor"] == 2
    assert [i["number"] for i in first["issues"]] == [numbers[2], numbers[1]]
    rest = api.get(
        f"/v1/repos/{REPO}/issues", params={"label": label, "q": "crash", "limit": 2, "cursor": 2}, headers=OPERATOR
    ).json()
    assert [i["number"] for i in rest["issues"]] == [numbers[0]] and rest["next_cursor"] is None
    unlabeled = api.get(f"/v1/repos/{REPO}/issues", params={"q": "something else"}, headers=OPERATOR).json()
    assert unlabeled["total"] >= 1
    assert api.get("/v1/repos/o/unknown/issues", headers=OPERATOR).status_code == 404


# --- The pull request activity map ----------------------------------------------------------------------


def test_activity_groups_each_tasks_pull_request_with_its_state_cost_and_latest_run(api, parts):
    from datetime import datetime, timezone

    events, _, tasks = parts
    goal = api.post(
        "/v1/goals",
        json={"title": "Saves", "statement": "Fix saves.", "repos": [REPO], "label": _label(), "start": False},
        headers=OPERATOR,
    ).json()
    now = datetime.now(timezone.utc).isoformat()
    base = {"repo": REPO, "goal_id": goal["id"], "created_at": now, "updated_at": now}
    tasks.tasks["t-a"] = {
        **base,
        "id": "t-a",
        "title": "Merged one",
        "status": "closed",
        "pr_url": "https://github.com/o/r/pull/1",
    }
    tasks.tasks["t-b"] = {
        **base,
        "id": "t-b",
        "title": "Waiting one",
        "status": "ready",
        "pr_url": "https://github.com/o/r/pull/2",
    }
    tasks.tasks["t-c"] = {**base, "id": "t-c", "title": "Failed one", "status": "failed", "pr_url": None}
    for task_id, run, tier, usd in (
        ("t-a", "r1", "standard", "1.00"),
        ("t-a", "r2", "fix", "0.50"),
        ("t-b", "r3", "standard", "2.00"),
        ("t-c", "r4", "standard", "0.25"),
    ):
        events.emit("attempt_finished", {"tier": tier, "spent_usd": usd}, task_id=task_id, run_id=run)
    events.emit("pull_request_merged", {"pr_url": "https://github.com/o/r/pull/1"}, task_id="t-a")

    shown = api.get("/v1/activity", params={"group_by": "goal"}, headers=OPERATOR).json()

    [row] = [g for g in shown["groups"] if g["key"] == goal["id"]]
    assert row["name"] == "Saves" and row["total"] == 2 and row["left_out"] == 0
    by_task = {p["task_id"]: p for p in row["prs"]}
    assert (
        by_task["t-a"]["state"],
        by_task["t-a"]["usd"],
        by_task["t-a"]["fix_rounds"],
        by_task["t-a"]["latest_run_id"],
    ) == ("merged", 1.5, 1, "r2")
    assert by_task["t-b"]["state"] == "waiting"
    assert [(p["task_id"], p["usd"]) for p in row["spent_without_pr"]] == [("t-c", 0.25)]
    assert shown["cost_scale"]["low"] <= shown["cost_scale"]["high"]
    assert api.get("/v1/activity", params={"group_by": "day"}, headers=OPERATOR).status_code == 422


# --- The repo registry from the console -----------------------------------------------------------------


class FakeApps:
    async def installed(self):
        return [
            {"name": REPO, "private": False, "install_url": "https://github.com/apps/mh/installations/new"},
            {
                "name": "davecthomas/new-thing",
                "private": True,
                "install_url": "https://github.com/apps/mh/installations/new",
            },
        ]


def test_available_repos_are_the_installed_ones_the_registry_lacks(parts):
    events, tracker, tasks = parts
    app = create_app(
        events,
        tracker,
        tasks,
        operator_keys={"op-key": "dave"},
        service_keys={},
        sweep_seconds=0,
        titler=_no_title,
        apps=FakeApps(),
    )
    with TestClient(app) as client:
        shown = client.get("/v1/repos/available", headers=OPERATOR).json()
    assert shown == {
        "repos": [{"name": "davecthomas/new-thing", "private": True}],
        "install_urls": ["https://github.com/apps/mh/installations/new"],
    }


def test_a_repo_an_open_goal_names_cannot_leave_the_registry(api, monkeypatch):
    from meta_harness.config import kinds

    registry = {"repos": {REPO: {"tracker": "github", "default_checks": ["true"]}, "o/spare": {"tracker": "github"}}}
    monkeypatch.setitem(
        kinds.KINDS, "repos", dataclasses.replace(kinds.KINDS["repos"], seed=lambda: copy.deepcopy(registry))
    )
    goal = api.post(
        "/v1/goals",
        json={"title": "Busy", "statement": "A.", "repos": [REPO], "label": _label(), "start": False},
        headers=OPERATOR,
    ).json()

    refused = api.put(
        "/v1/config/repos", json={"document": {"repos": {"o/spare": {"tracker": "github"}}}}, headers=OPERATOR
    )

    assert refused.status_code == 422
    assert any(goal["id"] in reason and REPO in reason for reason in refused.json()["detail"]["reasons"])
    api.post(f"/v1/goals/{goal['id']}/cancel", headers=OPERATOR)
