"""A feature goal plans its issues, and nothing runs until an operator approves the plan.

The planner's API runs against Postgres with fakes for the tracker, the Task API and the event service. No
model, no GitHub and no run is called: the planning task's drafts are events the test records.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from meta_harness import outbound
from meta_harness.trackers import TrackerError
from meta_harness.planner.service import create_app
from meta_harness.planner.tracker import Issue
from meta_harness.run_service import provenance

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
            raise TrackerError("403 Resource not accessible by integration", retryable=False, status=403)
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
    assert f"\n\nDepends on: #{store_issue.number}\n" in api_issue.body
    assert f"\n\nDepends on: #{api_issue.number}\n" in ui_issue.body
    # Each issue ends with where it came from: the goal, the planning task that drafted it, who approved it.
    [plan_task] = [t["id"] for t in tasks.tasks.values() if t["task_type"] == "plan"]
    for issue in tracker.created:
        [said] = provenance.parse(issue.body)
        assert (said["kind"], said["goal"], said["task"], said["by"]) == ("issue", goal["id"], plan_task, "dave")
        assert issue.body.endswith(provenance.line(provenance.Provenance(**said)))
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
    assert "the issue tracker refused after creating 1 of 2 issues" in refused.json()["detail"]
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
    assert (
        store_issue.body.startswith(f"{plain}\n\nProvenance: meta-harness ") and "rich tapestry" not in store_issue.body
    )
    assert f"\n\nDepends on: #{store_issue.number}\n" in api_issue.body
    checked = [p for t, p in events.events if t == "writing_checked"]
    assert [(p["kind"], p["draft_id"], p["kept"], p["goal_id"]) for p in checked] == [
        ("issue", "store", 1, goal["id"]),
        ("issue", "api", 0, goal["id"]),
    ]
    assert checked[0]["changes"] == [{"before": sloppy, "after": plain}]


# --- A goal that uses no issue tracker ------------------------------------------------------------------


class GitHubIssues(WritingTracker):
    scheme = "github"
    writes_progress = False

    def bound(self, source):
        return self

    def repo_of(self, ref: str) -> str:
        from meta_harness.trackers.github import parse_ref

        return parse_ref(ref)[0]


@pytest.fixture
def untracked(monkeypatch):
    """A planner with GitHub set up as the default tracker, and the meta-harness's own tracker beside it."""
    from meta_harness.trackers import OwnTracker, Trackers
    from meta_harness.trackers import registry as tracker_registry

    document = {"default": "github", "trackers": {"github": {}}}
    monkeypatch.setattr(tracker_registry, "document", lambda: document)
    events, github, tasks = Events(), GitHubIssues(), FakeTasks()
    app = create_app(
        events,
        Trackers([github, OwnTracker()]),
        tasks,
        operator_keys={"op-key": "dave"},
        service_keys={},
        sweep_seconds=0,
        titler=_no_title,
    )
    with TestClient(app) as client:
        yield client, events, github, tasks, document


def test_a_goal_with_no_tracker_is_planned_and_its_tasks_are_kept_in_the_meta_harness_alone(untracked):
    client, events, github, tasks, _ = untracked
    goal = _feature(client, tracker="mh")
    assert (goal["tracker"], goal["status"]) == ("mh", "planning")
    _planned(client, events, tasks, goal, [draft("api", ("store",)), draft("store")])

    approved = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR)

    assert approved.status_code == 200, approved.text
    created = approved.json()["plan"]["created"]
    assert all(ref.startswith("mh:i-") for ref in created.values())
    assert github.created == [] and github.labels == [], "nothing is written to any outside tracker"
    swept = client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR).json()
    by_ref = {
        t["external_ref"]: t
        for t in tasks.tasks.values()
        if t.get("goal_id") == goal["id"] and t["task_type"] != "plan"
    }
    assert set(by_ref) == set(created.values()), swept
    # The plan's order holds: the API waits on the store.
    store_task, api_task = by_ref[created["store"]], by_ref[created["api"]]
    assert (store_task["status"], api_task["status"]) == ("queued", "blocked")
    assert api_task["depends_on"] == [created["store"]] and store_task["acceptance_checks"] == ["make test"]

    # Each task's merge closes its issue here, since no tracker would, and the goal is met when all have.
    for task in (store_task, api_task):
        task.update(status="closed", pr_url="https://github.com/davecthomas/meta-harness/pull/9")
        client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR)
    after = client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()
    assert {i["state"] for i in after["issues"]} == {"closed"} and after["status"] == "met"


def test_a_goal_with_no_tracker_plans_from_its_description_and_takes_no_outside_issue(untracked):
    client, _, github, _, _ = untracked
    refused = client.post(
        "/v1/goals", json={"statement": "Fix bugs", "repos": [REPO], "tracker": "mh", "label": "bug"}, headers=OPERATOR
    )
    assert refused.status_code == 422
    assert "a goal that uses no issue tracker has no issues to take in" in refused.json()["detail"]
    assert "plan it from the description" in refused.json()["detail"]

    theirs = github.add(REPO, 1_000_000 + uuid.uuid4().int % 10**12)
    outside = client.post(
        "/v1/goals",
        json={"statement": "Add exports", "repos": [REPO], "modality": "feature", "tracker": "mh", "refs": [theirs]},
        headers=OPERATOR,
    )
    assert outside.status_code == 422 and "is not a mh issue" in outside.json()["detail"]


def test_with_no_tracker_set_up_a_new_goal_uses_none(untracked):
    client, _, _, _, document = untracked
    document.update(trackers={}, default=None)

    listed = client.get("/v1/trackers", headers=OPERATOR).json()
    goal = _feature(client)

    assert listed["default"] == "mh" and [t["scheme"] for t in listed["trackers"] if t["configured"]] == ["mh"]
    assert goal["tracker"] == "mh"


def test_a_tracked_goal_may_hold_a_task_kept_in_the_meta_harness_beside_its_trackers_issues(untracked):
    import asyncio

    from meta_harness.trackers import OwnTracker

    client, _, github, tasks, _ = untracked
    label = f"mh:{uuid.uuid4().hex[:8]}"
    theirs = github.add(
        REPO, 1_000_000 + uuid.uuid4().int % 10**12, labels=[label], body="## Acceptance checks\n\n- make test"
    )
    kept = asyncio.run(
        OwnTracker().create_issue(
            REPO, outbound.trusted("Kept here"), outbound.trusted("Do it.\n\n## Acceptance checks\n\n- make test\n"), []
        )
    )
    goal = client.post(
        "/v1/goals",
        json={"statement": "Fix bugs", "repos": [REPO], "label": label, "refs": [kept.ref]},
        headers=OPERATOR,
    ).json()
    assert goal["tracker"] == "github"

    client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR)

    refs = {t["external_ref"]: t for t in tasks.tasks.values() if t.get("goal_id") == goal["id"]}
    assert set(refs) == {theirs, kept.ref}
    refs[kept.ref].update(status="closed", pr_url="https://github.com/davecthomas/meta-harness/pull/9")
    client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR)
    states = {i["ref"]: i["state"] for i in client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["issues"]}
    assert states == {theirs: "open", kept.ref: "closed"}


# --- Every goal is planned: a goal that already has issues --------------------------------------------

CHECKS = "## Acceptance checks\n\n- make test"


@pytest.fixture
def planned(untracked, monkeypatch):
    """The untracked world, with the planning configuration saying every goal is planned."""
    from meta_harness.planner import planning

    monkeypatch.setattr(planning, "plans_every_goal", lambda: True)
    return untracked


def _labeled(client, github, titles: list[str], **fields) -> tuple[dict, list[str]]:
    """A goal on GitHub with a label, and an open issue with that label for each title."""
    label = f"mh:{uuid.uuid4().hex[:8]}"
    refs = [
        github.add(REPO, 1_000_000 + uuid.uuid4().int % 10**12, labels=[label], body=f"Do it.\n\n{CHECKS}", title=t)
        for t in titles
    ]
    made = client.post(
        "/v1/goals", json={"statement": "Fix the console", "repos": [REPO], "label": label, **fields}, headers=OPERATOR
    )
    assert made.status_code == 201, made.text
    return made.json(), refs


def _plan(client, events, tasks, goal: dict, recorded: list[tuple[str, dict]]) -> dict:
    """The goal's planning task ends having recorded these (event type, payload). Returns what the API shows."""
    task = next(t for t in tasks.tasks.values() if t.get("goal_id") == goal["id"] and t["status"] == "queued")
    for event_type, payload in recorded:
        events.emit(event_type, payload, task_id=task["id"])
    task["status"] = "closed"
    return client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()


def test_a_goal_with_issues_is_planned_at_save_over_those_issues_and_nothing_starts(planned):
    client, events, github, tasks, _ = planned

    goal, (first, second) = _labeled(client, github, ["Crash on save", "Wrong total"])

    assert (goal["status"], goal["planned"], goal["started_at"]) == ("planning", True, None)
    [task] = [t for t in tasks.tasks.values() if t.get("goal_id") == goal["id"]]
    assert task["task_type"] == "plan"
    # The planning task reads the goal's issues together, each with its ref and text.
    for expected in (
        f"## {first} ({REPO}): Crash on save",
        f"## {second} ({REPO}): Wrong total",
        "plan_issue",
        "plan_summary",
        "leave_out",
    ):
        assert expected in task["description"]
    assert "The goal already has 2 issues" in task["description"]
    assert client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR).status_code in (200, 409)
    assert [t["task_type"] for t in tasks.tasks.values() if t.get("goal_id") == goal["id"]] == ["plan"]


def test_the_plan_holds_a_decision_for_each_issue_the_new_tasks_the_summary_and_the_gaps(planned):
    client, events, github, tasks, _ = planned
    goal, (keep, big, dup) = _labeled(client, github, ["Keep me", "Too big", "Duplicate"])

    shown = _plan(
        client,
        events,
        tasks,
        goal,
        [
            ("issue_drafted", draft("store")),
            ("issue_drafted", draft("api", ("store", keep))),
            (
                "issue_planned",
                {"ref": big, "decision": "split", "reason": "Two changes.", "replaced_by": ["store", "api"]},
            ),
            ("issue_planned", {"ref": dup, "decision": "leave_out", "reason": "Same as the first."}),
            ("plan_summarized", {"summary": "Store, then the API.", "gaps": ["Nothing covers export."]}),
        ],
    )

    plan = shown["plan"]
    assert (
        plan["invalid"] == []
        and plan["summary"] == "Store, then the API."
        and plan["gaps"] == ["Nothing covers export."]
    )
    assert [(i["ref"], i["title"]) for i in plan["issues"]] == [(keep, "Keep me"), (big, "Too big"), (dup, "Duplicate")]
    # An issue the planner said nothing of is kept.
    assert [(i["ref"], i["decision"]) for i in plan["items"]] == [(keep, "keep"), (big, "split"), (dup, "leave_out")]
    assert [d["draft_id"] for d in plan["drafts"]] == ["store", "api"]

    # An operator changes a decision: the next version keeps the planner's summary, and says what is now wrong.
    edited = client.put(
        f"/v1/goals/{goal['id']}/plan",
        json={"drafts": plan["drafts"], "items": [{"ref": big, "decision": "split", "reason": "x"}]},
        headers=OPERATOR,
    ).json()["plan"]
    assert edited["version"] == 2 and edited["summary"] == "Store, then the API."
    assert f"{big}: is split, so name the new tasks that replace it" in edited["invalid"]
    assert client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR).status_code == 409


def _decided(client, events, tasks, github, **approve) -> tuple[dict, dict, tuple]:
    goal, (keep, big, dup) = _labeled(client, github, ["Keep me", "Too big", "Duplicate"])
    _plan(
        client,
        events,
        tasks,
        goal,
        [
            ("issue_drafted", draft("store")),
            ("issue_drafted", draft("api", ("store",))),
            ("issue_planned", {"ref": keep, "decision": "keep", "reason": "Ready.", "depends_on": ["api"]}),
            (
                "issue_planned",
                {"ref": big, "decision": "split", "reason": "Two changes.", "replaced_by": ["store", "api"]},
            ),
            ("issue_planned", {"ref": dup, "decision": "leave_out", "reason": "Same as the first."}),
        ],
    )
    approved = client.post(f"/v1/goals/{goal['id']}/approve", json=approve or None, headers=OPERATOR)
    assert approved.status_code == 200, approved.text
    return goal, approved.json()["plan"], (keep, big, dup)


def test_approving_applies_the_plan_new_tasks_in_the_tracker_splits_replaced_and_duplicates_left_out(planned):
    client, events, github, tasks, _ = planned

    goal, plan, (keep, big, dup) = _decided(client, events, tasks, github)

    created = plan["created"]
    assert plan["tracked"] is True and [i.title for i in github.created] == ["Build store", "Build api"]
    assert all(ref.startswith("github:") for ref in created.values())
    client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR)
    by_ref = {
        t["external_ref"]: t
        for t in tasks.tasks.values()
        if t.get("goal_id") == goal["id"] and t["task_type"] != "plan"
    }
    # The kept issue and the two new tasks run. The split issue and the duplicate do not.
    assert set(by_ref) == {keep, created["store"], created["api"]}
    # The plan's order holds, across an existing issue and the new tasks.
    assert by_ref[created["store"]]["status"] == "queued"
    assert by_ref[created["api"]]["depends_on"] == [created["store"]]
    assert by_ref[keep]["depends_on"] == [created["api"]] and by_ref[keep]["status"] == "blocked"
    shown = {i["ref"]: i for i in client.get(f"/v1/goals/{goal['id']}/issues", headers=OPERATOR).json()["issues"]}
    assert dup not in shown, "an issue the plan left out is not the goal's"
    assert shown[big]["split_into"] == [created["store"], created["api"]]
    assert shown[big]["held_reason"].startswith(
        "the plan split it into 2 smaller tasks, which run in its place: Two changes."
    )
    after = client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()
    assert after["status"] == "active" and after["plan_approved_at"]


def test_new_tasks_can_be_kept_in_the_meta_harness_and_out_of_the_goals_tracker(planned):
    client, events, github, tasks, _ = planned

    goal, plan, (keep, _, _) = _decided(client, events, tasks, github, track=False)

    created = plan["created"]
    assert plan["tracked"] is False and github.created == [] and github.labels == []
    assert all(ref.startswith("mh:i-") for ref in created.values())
    client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR)
    by_ref = {
        t["external_ref"]: t
        for t in tasks.tasks.values()
        if t.get("goal_id") == goal["id"] and t["task_type"] != "plan"
    }
    # One goal now holds its tracker's issue and two tasks kept here, and the plan's order joins them.
    assert set(by_ref) == {keep, created["store"], created["api"]}
    assert by_ref[created["api"]]["depends_on"] == [created["store"]] and by_ref[keep]["depends_on"] == [created["api"]]


def test_an_issue_that_joins_after_approval_waits_as_a_proposed_addition(planned):
    client, events, github, tasks, _ = planned
    goal, _, _ = _decided(client, events, tasks, github)
    client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR)
    before = {t["external_ref"] for t in tasks.tasks.values() if t.get("goal_id") == goal["id"]}

    late = github.add(
        REPO,
        1_000_000 + uuid.uuid4().int % 10**12,
        labels=[goal["label"]],
        body=f"Later.\n\n{CHECKS}",
        title="Labeled later",
    )
    client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR)

    assert {t["external_ref"] for t in tasks.tasks.values() if t.get("goal_id") == goal["id"]} == before
    card = next(
        i for i in client.get(f"/v1/goals/{goal['id']}/issues", headers=OPERATOR).json()["issues"] if i["ref"] == late
    )
    assert "joined the goal after its plan was approved" in card["held_reason"] and "Run anyway" in card["held_reason"]

    # The operator adds it to the plan as it is.
    ran = client.post(f"/v1/goals/{goal['id']}/issues/run-anyway", json={"ref": late}, headers=OPERATOR)
    assert ran.status_code == 200, ran.text
    client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR)
    assert late in {t["external_ref"] for t in tasks.tasks.values() if t.get("goal_id") == goal["id"]}


def test_only_goals_that_plan_from_their_description_are_planned_when_the_configuration_says_so(untracked):
    client, _, github, tasks, _ = untracked

    goal, _ = _labeled(client, github, ["Crash on save"])

    assert (goal["status"], goal["planned"]) == ("active", False) and goal["started_at"]
    assert not [t for t in tasks.tasks.values() if t.get("task_type") == "plan" and t.get("goal_id") == goal["id"]]


def test_a_goal_under_way_is_planned_again_over_what_no_task_has_started_and_its_running_tasks_go_on(planned):
    client, events, github, tasks, _ = planned
    goal, _, (keep, big, dup) = _decided(client, events, tasks, github)
    client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR)
    running = {
        t["id"]: t["status"]
        for t in tasks.tasks.values()
        if t.get("goal_id") == goal["id"] and t["task_type"] != "plan"
    }
    late = github.add(
        REPO,
        1_000_000 + uuid.uuid4().int % 10**12,
        labels=[goal["label"]],
        body=f"Later.\n\n{CHECKS}",
        title="Labeled later",
    )

    again = client.post(f"/v1/goals/{goal['id']}/replan", json={"note": "Fit the new issue in."}, headers=OPERATOR)

    assert again.status_code == 202, again.text
    assert client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["status"] == "planning"
    plan_tasks = [t for t in tasks.tasks.values() if t.get("goal_id") == goal["id"] and t["task_type"] == "plan"]
    newest = max(plan_tasks, key=lambda t: t["external_ref"])
    # The planner is given what no task has started on: the late issue. What is under way is not its to decide.
    assert "The goal already has 1 issue" in newest["description"] and "Labeled later" in newest["description"]
    assert "Keep me" not in newest["description"] and "Fit the new issue in." in newest["description"]
    # Nothing that was running is touched.
    assert {t["id"]: t["status"] for t in tasks.tasks.values() if t["id"] in running} == running

    newest["status"] = "closed"
    events.emit("issue_planned", {"ref": late, "decision": "keep", "reason": "Fits."}, task_id=newest["id"])
    shown = client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()
    assert [i["ref"] for i in shown["plan"]["issues"]] == [late] and shown["plan"]["invalid"] == []
    assert client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR).status_code == 200
    client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR)
    assert late in {t["external_ref"] for t in tasks.tasks.values() if t.get("goal_id") == goal["id"]}


def test_a_goal_that_was_never_planned_or_has_ended_cannot_be_planned_again(untracked):
    client, _, github, _, _ = untracked
    goal, _ = _labeled(client, github, ["Crash on save"])

    refused = client.post(f"/v1/goals/{goal['id']}/replan", headers=OPERATOR)
    assert refused.status_code == 409 and "cannot be planned again: it was never planned" in refused.json()["detail"]
    client.post(f"/v1/goals/{goal['id']}/cancel", headers=OPERATOR)
    ended = client.post(f"/v1/goals/{goal['id']}/replan", headers=OPERATOR)
    assert ended.status_code == 409 and "it is canceled" in ended.json()["detail"]
