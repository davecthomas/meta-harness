"""A feature goal plans its issues, and nothing runs until an operator approves the plan.

The planner's API runs against Postgres with fakes for the tracker, the Task API and the event service. No
model, no GitHub and no run is called: the planning task's drafts are events the test records.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from meta_harness.github_app import GitHubAppError
from meta_harness.planner.service import create_app
from meta_harness.planner.tracker import Issue

from .test_planner import OPERATOR, Events, FakeTasks, FakeTracker, _no_title, migrated, registry  # noqa: F401

REPO = "davecthomas/meta-harness"


class WritingTracker(FakeTracker):
    """A tracker that creates issues too, numbered from a base no other test uses."""

    def __init__(self):
        super().__init__()
        self.labels: list[tuple[str, str]] = []
        self.created: list[Issue] = []
        self.next = 1_000_000 + uuid.uuid4().int % 10**15
        # Refuse the nth create_issue call (from 1) once, as GitHub would.
        self.refuse_at: int | None = None

    async def ensure_label(self, repo, label):
        self.labels.append((repo, label))

    async def create_issue(self, repo, title, body, labels):
        if self.refuse_at == len(self.created) + 1:
            self.refuse_at = None
            raise GitHubAppError("403 Resource not accessible by integration", retryable=False, status=403)
        self.next += 1
        ref = f"github:{repo}#{self.next}"
        issue = Issue(ref=ref, repo=repo, number=self.next, title=title, body=body, labels=list(labels), state="open")
        self.issues[ref] = issue
        self.created.append(issue)
        return issue


@pytest.fixture
def world():
    events, tracker, tasks = Events(), WritingTracker(), FakeTasks()
    app = create_app(
        events, tracker, tasks, operator_keys={"op-key": "dave"}, service_keys={}, sweep_seconds=0, titler=_no_title
    )
    with TestClient(app) as client:
        yield client, events, tracker, tasks


def draft(draft_id: str, depends_on: tuple = (), **fields) -> dict:
    body = f"Build {draft_id}.\n\n## Acceptance checks\n\n- make test"
    return {
        "draft_id": draft_id,
        "repo": REPO,
        "title": f"Build {draft_id}",
        "body": body,
        "labels": [],
        "depends_on": list(depends_on),
        **fields,
    }


def _feature(client, **fields) -> dict:
    body = {"statement": "Add exports", "repos": [REPO], "modality": "feature", **fields}
    created = client.post("/v1/goals", json=body, headers=OPERATOR)
    assert created.status_code == 201, created.text
    return created.json()


def _planned(client, events, tasks, goal: dict, drafts: list[dict], status: str = "closed") -> dict:
    """The goal's planning task ends having drafted these. Returns the plan the API then shows."""
    task = next(t for t in tasks.tasks.values() if t.get("goal_id") == goal["id"] and t["status"] == "queued")
    for d in drafts:
        events.emit("issue_drafted", d, task_id=task["id"])
    task["status"] = status
    return client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()


def test_a_feature_goal_starts_in_planning_with_its_own_label_and_one_planning_task(world):
    client, events, _, tasks = world

    goal = _feature(client)

    assert goal["status"] == "planning" and goal["started_at"] is None
    assert goal["label"] == f"meta-harness:goal-{goal['id'][:8]}"
    [task] = [t for t in tasks.tasks.values() if t.get("goal_id") == goal["id"]]
    assert task["task_type"] == "plan" and task["pin"].startswith("harness:pi-plan/")
    assert task["repo"] == REPO and "Add exports" in task["description"] and "create_issue" in task["description"]
    shown = client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()
    assert shown["plan"] is None and shown["planning_task_id"] == task["id"]
    assert "plan_requested" in events.types()


def test_the_planning_tasks_drafts_become_plan_version_1(world):
    client, events, _, tasks = world
    goal = _feature(client)

    shown = _planned(
        client,
        events,
        tasks,
        goal,
        [draft("store"), draft("api", ("store",)), draft("api", ("store",), title="The API")],
    )

    plan = shown["plan"]
    assert (plan["version"], plan["source"], plan["invalid"]) == (1, "planned", [])
    assert [(d["draft_id"], d["title"]) for d in plan["drafts"]] == [("store", "Build store"), ("api", "The API")]
    assert shown["planning_task_id"] is None
    assert (
        "plan_saved",
        {"goal_id": goal["id"], "version": 1, "source": "planned", "drafts": 2, "invalid": []},
    ) in events.events
    # Asking again saves nothing more.
    again = client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()
    assert again["plan"]["version"] == 1


def test_a_planning_task_that_failed_leaves_a_plan_that_says_why_and_can_be_replanned(world):
    client, events, _, tasks = world
    goal = _feature(client)

    shown = _planned(client, events, tasks, goal, [], status="failed")

    assert shown["plan"]["drafts"] == [] and "the planning task ended failed" in shown["plan"]["invalid"][0]
    refused = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR)
    assert refused.status_code == 409 and "cannot be approved" in refused.json()["detail"]

    replanned = client.post(f"/v1/goals/{goal['id']}/replan", json={"note": "Smaller issues."}, headers=OPERATOR)
    assert replanned.status_code == 202
    second = tasks.tasks[replanned.json()["planning_task_id"]]
    assert "Smaller issues." in second["description"] and second["external_ref"] != "plan"
    busy = client.post(f"/v1/goals/{goal['id']}/replan", headers=OPERATOR)
    assert busy.status_code == 409


def test_an_edited_plan_is_a_new_version_and_an_invalid_one_cannot_be_approved(world):
    client, events, _, tasks = world
    goal = _feature(client)
    _planned(client, events, tasks, goal, [draft("store")])

    edited = client.put(
        f"/v1/goals/{goal['id']}/plan", json={"drafts": [draft("store", ("ghost",)), draft("ui")]}, headers=OPERATOR
    ).json()

    assert (edited["plan"]["version"], edited["plan"]["source"], edited["plan"]["created_by"]) == (2, "edited", "dave")
    assert edited["plan"]["invalid"] == ["store: depends on ghost, which is no draft and no issue (owner/repo#number)"]
    refused = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR)
    assert refused.status_code == 409 and "ghost" in refused.json()["detail"]
    assert client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["status"] == "planning"


def test_approving_creates_the_issues_in_order_with_the_goals_label_and_starts_the_goal(world):
    client, events, tracker, tasks = world
    goal = _feature(client)
    _planned(
        client,
        events,
        tasks,
        goal,
        [draft("ui", ("api",), labels=["frontend"]), draft("api", ("store",)), draft("store")],
    )

    approved = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR)

    assert approved.status_code == 200, approved.text
    assert [i.title for i in tracker.created] == ["Build store", "Build api", "Build ui"]
    store_issue, api_issue, ui_issue = tracker.created
    assert tracker.labels == [(REPO, goal["label"])]
    assert ui_issue.labels == [goal["label"], "frontend"] and store_issue.labels == [goal["label"]]
    assert api_issue.body.endswith(f"Depends on: #{store_issue.number}\n")
    assert ui_issue.body.endswith(f"Depends on: #{api_issue.number}\n")
    assert approved.json()["plan"]["created"] == {"store": store_issue.ref, "api": api_issue.ref, "ui": ui_issue.ref}
    after = client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()
    assert after["status"] == "active" and after["started_at"] and after["plan_approved_at"]
    assert {i["ref"] for i in after["issues"]} == {store_issue.ref, api_issue.ref, ui_issue.ref}
    assert "plan_approved" in events.types()
    # The plan is settled: it is read, not changed.
    assert client.put(f"/v1/goals/{goal['id']}/plan", json={"drafts": []}, headers=OPERATOR).status_code == 409


def test_an_approval_github_interrupts_carries_on_without_making_an_issue_twice(world):
    client, events, tracker, tasks = world
    goal = _feature(client)
    _planned(client, events, tasks, goal, [draft("store"), draft("api", ("store",))])
    tracker.refuse_at = 2

    refused = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR)

    assert refused.status_code == 502
    assert "GitHub refused after creating 1 of 2 issues" in refused.json()["detail"]
    assert client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["status"] == "planning"

    again = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR)
    assert again.status_code == 200
    assert [i.title for i in tracker.created] == ["Build store", "Build api"], "store was not created twice"


def test_a_feature_goal_saved_without_starting_plans_when_it_is_first_resumed(world):
    client, _, _, tasks = world
    goal = _feature(client, start=False)
    assert goal["status"] == "paused" and not tasks.tasks

    resumed = client.post(f"/v1/goals/{goal['id']}/resume", headers=OPERATOR).json()

    assert resumed["status"] == "planning"
    assert [t["task_type"] for t in tasks.tasks.values()] == ["plan"]


def test_a_fix_goal_still_needs_a_label_or_issues_and_never_plans(world):
    client, _, _, tasks = world
    bare = client.post("/v1/goals", json={"statement": "Fix bugs", "repos": [REPO]}, headers=OPERATOR)
    assert bare.status_code == 422
    fix = client.post(
        "/v1/goals", json={"statement": "Fix bugs", "repos": [REPO], "label": "bug-x"}, headers=OPERATOR
    ).json()
    assert fix["status"] == "active" and not tasks.tasks
    assert client.post(f"/v1/goals/{fix['id']}/replan", headers=OPERATOR).status_code == 409


def test_an_approved_drafts_body_goes_through_the_writing_pass_before_it_becomes_an_issue(world, monkeypatch):
    from meta_harness.run_service import writing

    client, events, tracker, tasks = world
    goal = _feature(client)
    sloppy = "This robust export weaves a rich tapestry.\n\n## Acceptance checks\n\n- make test"
    plain = "The export writes one file per day.\n\n## Acceptance checks\n\n- make test"
    _planned(client, events, tasks, goal, [draft("store", body=sloppy), draft("api", ("store",))])

    async def check_all(texts, transport=None):
        return [
            writing.Outcome(text=plain, findings=2, rewritten=True, kept=True, before=text, usd_cost="0.002")
            if text == sloppy
            else writing.Outcome(text=text)
            for text in texts
        ]

    monkeypatch.setattr(writing, "check_all", check_all)

    approved = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR)

    assert approved.status_code == 200, approved.text
    store_issue, api_issue = tracker.created
    assert store_issue.body == f"{plain}\n" and "rich tapestry" not in store_issue.body
    assert api_issue.body.endswith(f"Depends on: #{store_issue.number}\n")
    checked = [p for t, p in events.events if t == "writing_checked"]
    assert [(p["kind"], p["draft_id"], p["kept"], p["goal_id"]) for p in checked] == [
        ("issue", "store", 1, goal["id"]),
        ("issue", "api", 0, goal["id"]),
    ]
    assert checked[0]["changes"] == [{"before": sloppy, "after": plain}]
