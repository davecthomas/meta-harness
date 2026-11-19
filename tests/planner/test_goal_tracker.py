"""A goal uses one issue tracker: the default when it is created unless it names another, and keeps it."""

import uuid

import pytest
from fastapi.testclient import TestClient

from meta_harness.planner.service import create_app
from meta_harness.trackers import Issue, TrackerError, Trackers
from meta_harness.trackers import registry as tracker_registry

from .test_planner import OPERATOR, Events, FakeTasks, FakeTracker, _no_title, migrated, registry  # noqa: F401

pytestmark = pytest.mark.db

REPO = "davecthomas/meta-harness"


class FakeJira(FakeTracker):
    """Jira as the planner sees it: refs are jira:<KEY>, and a project's issues go to one repo."""

    scheme = "jira"
    writes_progress = True

    def __init__(self) -> None:
        super().__init__()
        self.told: list[tuple] = []
        self.refuse_comments = False
        # The refs assigned to the meta-harness's Jira account.
        self.assigned: set[str] = set()

    async def set_status(self, ref: str, status: str) -> bool:
        self.told.append(("status", ref, status))
        if status == "done":
            self.issues[ref].state = "closed"
        return True

    async def link_pull_request(self, ref: str, pr_url: str, title: str) -> None:
        self.told.append(("link", ref, pr_url))

    async def comment(self, ref: str, body: str) -> str:
        if self.refuse_comments:
            raise TrackerError("Jira refused POST /issue/comment (403): no permission", retryable=False, status=403)
        self.told.append(("comment", ref, body))
        return ""

    def add_key(self, key: str, labels: list[str], title: str = "From Jira") -> str:
        ref = f"jira:{key}"
        self.issues[ref] = Issue(
            ref=ref, repo=REPO, number=int(key.split("-")[1]), title=title, body="", labels=labels, state="open",
            checks=["make test"],
        )  # fmt: skip
        return ref

    async def list_open(self, repo, label):
        # As the adapter does: an issue that names none of the goal's repos comes with the first repo's.
        first = repo == self.source.repos[0]
        return [
            i
            for i in self.issues.values()
            if (i.repo == repo or (first and not i.repo)) and i.state == "open" and (label is None or label in i.labels)
        ]

    async def list_assigned(self, repo):
        # As the adapter does: only for a goal that asked, and whatever the issues' labels.
        if self.source.settings.get("assigned") != "yes":
            return []
        return [i for i in self.issues.values() if i.repo == repo and i.state == "open" and i.ref in self.assigned]

    def bound(self, source):
        self.source = source
        return self

    def repo_of(self, ref: str) -> str:
        if not ref.startswith(f"jira:{self.source.settings['project']}-"):
            raise ValueError(f"{ref} is not in {self.source.settings['project']}, the goal's Jira project")
        return self.source.repos[0]


class FakeGitHub(FakeTracker):
    scheme = "github"

    def bound(self, source):
        return self

    def repo_of(self, ref: str) -> str:
        from meta_harness.trackers.github import parse_ref

        return parse_ref(ref)[0]


@pytest.fixture
def world(monkeypatch):
    """A planner with GitHub and Jira, both set up, and Jira the default."""
    document = {
        "default": "jira",
        "trackers": {"github": {}, "jira": {"site": "https://example.atlassian.net"}},
    }  # fmt: skip
    monkeypatch.setattr(tracker_registry, "document", lambda: document)
    github, jira, tasks = FakeGitHub(), FakeJira(), FakeTasks()
    trackers = Trackers([github, jira])
    app = create_app(
        Events(), trackers, tasks, operator_keys={"op-key": "dave@example.com"}, service_keys={}, sweep_seconds=0,
        titler=_no_title,
    )  # fmt: skip
    with TestClient(app) as client:
        yield client, github, jira, tasks, document


def _goal(client, **fields) -> dict:
    label = f"mh:{uuid.uuid4().hex[:8]}"
    body = {"statement": "Fix bugs", "repos": [REPO], "label": label, "start": False, **fields}
    if body.get("tracker", "jira") == "jira":
        # A goal that uses Jira says which of the site's projects its issues are in.
        body.setdefault("tracker_settings", {"project": "VL"})
    made = client.post("/v1/goals", json=body, headers=OPERATOR)
    assert made.status_code == 201, made.text
    return made.json()


def test_a_goal_starts_with_jiras_default_project_and_names_only_a_listed_one(world):
    client, _, _, _, document = world
    document["trackers"]["jira"] = {
        "site": "https://example.atlassian.net",
        "default_project": "VL",
        "projects": {"VL": {"issue_type": "Story"}, "OPS": {}},
    }
    listed = {t["scheme"]: t for t in client.get("/v1/trackers", headers=OPERATOR).json()["trackers"]}
    assert listed["jira"]["goal_defaults"] == {"project": "VL"} and listed["github"]["goal_defaults"] == {}
    projects = listed["jira"]["settings"][1]
    assert (projects["kind"], projects["entry"]) == ("entries", "project")
    assert [s["name"] for s in projects["settings"]] == ["issue_type", "statuses"]

    # A goal that names no project takes the default; one may name another listed project.
    assert _goal(client, tracker_settings={})["tracker_settings"] == {"project": "VL"}
    assert _goal(client, tracker_settings={"project": "OPS"})["tracker_settings"] == {"project": "OPS"}
    body = {
        "statement": "Fix bugs",
        "repos": [REPO],
        "label": "mh:x",
        "start": False,
        "tracker_settings": {"project": "AB"},
    }
    refused = client.post("/v1/goals", json=body, headers=OPERATOR)
    assert refused.status_code == 422
    assert "AB is not one of the Jira projects the meta-harness works in (OPS, VL)" in refused.json()["detail"]


def test_a_project_an_open_goal_uses_cannot_leave_the_trackers_configuration(world):
    client, _, _, _, document = world
    jira_settings = {
        "site": "https://example.atlassian.net",
        "default_project": "VL",
        "projects": {"VL": {}, "OPS": {}},
    }
    document["trackers"]["jira"] = jira_settings
    goal = _goal(client, tracker_settings={"project": "OPS"})

    without = {**document, "trackers": {"github": {}, "jira": {**jira_settings, "projects": {"VL": {}}}}}
    refused = client.put("/v1/config/trackers", json={"document": without}, headers=OPERATOR)

    assert refused.status_code == 422
    [reason] = [r for r in refused.json()["detail"]["reasons"] if goal["id"] in r]
    assert reason.startswith("jira: OPS is not one of the Jira projects the meta-harness works in (VL)")
    assert reason.endswith("uses it")
    client.post(f"/v1/goals/{goal['id']}/cancel", headers=OPERATOR)


def test_a_new_goal_takes_the_default_tracker_and_reads_its_issues_from_it(world):
    client, github, jira, tasks, _ = world
    goal = _goal(client)
    assert (goal["tracker"], goal["tracker_settings"]) == ("jira", {"project": "VL"})
    key = f"VL-{1_000_000 + uuid.uuid4().int % 10**12}"
    ref = jira.add_key(key, [goal["label"]])
    github.add(REPO, 1_000_000 + uuid.uuid4().int % 10**12, labels=[goal["label"]])

    assert client.post(f"/v1/goals/{goal['id']}/resume", headers=OPERATOR).status_code == 200
    swept = client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR).json()

    held = [i["ref"] for i in client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["issues"]]
    assert held == [ref], f"only the Jira issue joins a Jira goal: {swept}"
    assert [t["external_ref"] for t in tasks.tasks.values() if t["goal_id"] == goal["id"]] == [ref]
    assert client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["tracker"] == "jira"


def test_a_goal_may_name_another_tracker_that_is_set_up_and_holds_only_its_issues(world):
    client, _, _, _, document = world
    goal = _goal(client, tracker="github")
    assert goal["tracker"] == "github"

    wrong = client.post(f"/v1/goals/{goal['id']}/issues", json={"refs": ["jira:VL-5"]}, headers=OPERATOR)
    assert wrong.status_code == 422 and "is not a github issue, and the goal's tracker is github" in wrong.text
    jira_goal = {"statement": "Mixed", "repos": [REPO], "tracker_settings": {"project": "VL"}, "start": False}
    mixed = client.post("/v1/goals", json={**jira_goal, "refs": [f"github:{REPO}#5"]}, headers=OPERATOR)
    assert mixed.status_code == 422 and "is not a jira issue, and the goal's tracker is jira" in mixed.text
    other = client.post("/v1/goals", json={**jira_goal, "refs": ["jira:OPS-5"]}, headers=OPERATOR)
    assert other.status_code == 422 and "is not in VL, the goal's Jira project" in other.text
    # What the tracker asks of a goal is asked before the goal exists.
    unnamed = client.post(
        "/v1/goals", json={"statement": "x", "repos": [REPO], "label": "mh:x", "start": False}, headers=OPERATOR
    )
    assert unnamed.status_code == 422 and "a goal that uses Jira names its project by its key" in unnamed.text

    del document["trackers"]["github"]
    refused = client.post(
        "/v1/goals",
        json={"statement": "x", "repos": [REPO], "label": "mh:x", "tracker": "github", "start": False},
        headers=OPERATOR,
    )
    assert refused.status_code == 422
    assert "the tracker 'github' is not set up (set up: jira). Set it up under Configuration" in refused.text
    unknown = client.post(
        "/v1/goals",
        json={"statement": "x", "repos": [REPO], "label": "mh:x", "tracker": "linear", "start": False},
        headers=OPERATOR,
    )
    assert unknown.status_code == 422 and "is not set up" in unknown.text


def test_the_trackers_are_listed_as_their_adapters_describe_them_and_issue_lists_name_one(world, monkeypatch):
    client, github, jira, _, _ = world
    monkeypatch.delenv("MH_JIRA_TOKEN", raising=False)
    monkeypatch.setenv("MH_JIRA_EMAIL", "bot@example.com")

    listed = client.get("/v1/trackers", headers=OPERATOR).json()

    assert listed["default"] == "jira"
    by = {t["scheme"]: t for t in listed["trackers"]}
    assert list(by) == ["github", "jira", "mh"]
    # The meta-harness's own tracker is the choice of no tracker: always there, with nothing to set up.
    assert (by["mh"]["built_in"], by["mh"]["configured"], by["mh"]["settings"]) == (True, True, [])
    assert by["mh"]["title"] == "None: no issue tracking" and not by["github"]["built_in"]
    assert by["github"]["configured"] and by["github"]["signed_in"] and not by["github"]["writes_progress"]
    assert [s["name"] for s in by["jira"]["settings"]] == ["site", "projects", "default_project"]
    assert by["jira"]["configured"] and not by["jira"]["signed_in"]
    assert by["jira"]["missing_credentials"] == ["MH_JIRA_TOKEN"] and by["jira"]["writes_progress"]

    label = f"mh:{uuid.uuid4().hex[:8]}"
    jira.add_key("VL-77", [label])
    github.add(REPO, 78, labels=[label])
    assert [s["name"] for s in by["jira"]["goal_settings"]] == ["project", "assigned"] and by["github"][
        "goal_settings"
    ] == []
    asking = {"label": label, "settings": '{"project": "VL"}'}
    default = client.get(f"/v1/repos/{REPO}/issues", params=asking, headers=OPERATOR).json()
    assert [i["ref"] for i in default["issues"]] == ["jira:VL-77"], "the default tracker's, when none is named"
    named = client.get(f"/v1/repos/{REPO}/issues", params={"label": label, "tracker": "github"}, headers=OPERATOR)
    assert [i["ref"] for i in named.json()["issues"]] == [f"github:{REPO}#78"]


def test_a_jira_issue_is_told_how_its_task_goes_once_per_change_and_done_closes_it(world):
    """Nothing in Jira shows a task's progress, or closes its issue, unless the planner says: each change is
    written once, a write that failed is tried again, and done is what lets the goal be met."""
    client, github, jira, tasks, _ = world
    goal = _goal(client, start=True)
    ref = jira.add_key(f"VL-{1_000_000 + uuid.uuid4().int % 10**12}", [goal["label"]])
    sweep = lambda: client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR).json()  # noqa: E731

    assert sweep()["told"] == [] and jira.told == [], "a task that has not started says nothing"
    [task] = [t for t in tasks.tasks.values() if t["goal_id"] == goal["id"]]

    task["status"] = "running"
    assert sweep()["told"] == [ref]
    assert jira.told == [
        ("status", ref, "in_progress"),
        ("comment", ref, f"The meta-harness started work on this issue (task {task['id']})."),
    ]
    assert sweep()["told"] == [] and len(jira.told) == 2, "nothing new, nothing written"

    pr = "https://github.com/davecthomas/meta-harness/pull/9"
    task.update(status="ready", pr_url=pr)
    jira.refuse_comments = True
    failed = sweep()
    assert failed["told"] == [] and any("could not tell its tracker in_review" in e for e in failed["errors"])
    jira.refuse_comments = False
    jira.told.clear()
    assert sweep()["told"] == [ref], "the write that failed is made again"
    assert jira.told == [
        ("status", ref, "in_review"),
        ("link", ref, pr),
        ("comment", ref, f"A pull request is open, and it is ready to merge: {pr}"),
    ]

    task["status"] = "closed"
    jira.told.clear()
    done = sweep()
    assert jira.told[0] == ("status", ref, "done") and jira.told[-1] == (
        "comment",
        ref,
        f"Done: the pull request merged. {pr}",
    )
    assert jira.issues[ref].state == "closed"
    assert client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["status"] in ("met", "active"), done

    # A GitHub goal's issues are told nothing: the pull request's Closes line does it.
    other = _goal(client, tracker="github", start=True)
    github.add(REPO, 1_000_000 + uuid.uuid4().int % 10**12, labels=[other["label"]])
    client.post(f"/v1/goals/{other['id']}/sweep", headers=OPERATOR)
    [started] = [t for t in tasks.tasks.values() if t["goal_id"] == other["id"]]
    started["status"] = "running"
    assert client.post(f"/v1/goals/{other['id']}/sweep", headers=OPERATOR).json()["told"] == []


def test_what_a_task_tells_its_issue():
    from meta_harness.planner.tracker_progress import told_for

    assert told_for({"id": "t-1", "status": "queued"}) is None
    assert told_for({"id": "t-1", "status": "ready"}) is None, "ready with no pull request has nothing to show"
    failed = told_for({"id": "t-1", "status": "failed", "status_reason": "its checks never passed"})
    assert (failed.key, failed.status) == ("failed", None)
    assert failed.comment == "The task failed: its checks never passed. An operator can retry it."
    pr = "https://github.com/o/r/pull/9"
    working = {"id": "t-1", "status": "running", "pr_url": pr}
    assert told_for(working).key == "in_progress", "the first word is that work started, pull request or not"
    assert told_for(working, "in_progress") is None, "the agents' own review of a new pull request says nothing"
    again = told_for(working, f"in_review:ready:{pr}")
    assert again.status == "in_progress" and again.key != "in_progress", "back at work after a person's review"
    assert told_for({**working, "resume": "merge"}, f"in_review:ready:{pr}") is None, (
        "a merge asked for is not new work"
    )
    assert told_for(working, again.key) is None
    assert told_for(working, "canceled").key == "in_progress", "taken up again after it ended"
    unresolved = told_for({"id": "t-1", "status": "review_unresolved", "pr_url": "https://github.com/o/r/pull/9"})
    assert "its review left findings for a person to settle" in unresolved.comment


def test_a_goal_with_several_repos_holds_a_jira_issue_until_it_names_its_repo(world):
    """One Jira project can hold work for several repos, so the issue says which one; until it does it waits."""
    client, _, jira, tasks, _ = world
    other = "davecthomas/ai-api-unified"
    goal = _goal(client, repos=[REPO, other], start=True)
    ref = jira.add_key(f"VL-{1_000_000 + uuid.uuid4().int % 10**12}", [goal["label"]])
    jira.issues[ref].repo = ""

    swept = client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR).json()

    assert swept["held"] == [ref] and not [t for t in tasks.tasks.values() if t["goal_id"] == goal["id"]]
    [card] = client.get(f"/v1/goals/{goal['id']}/issues", headers=OPERATOR).json()["issues"]
    assert card["held_reason"].startswith(f"it names none of the goal's repos ({REPO}, {other})")
    assert "add a line `Repo: <owner/name>`" in card["held_reason"]

    jira.issues[ref].repo = other
    client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR)
    [task] = [t for t in tasks.tasks.values() if t["goal_id"] == goal["id"]]
    assert (task["external_ref"], task["repo"]) == (ref, other)


def test_an_issue_a_canceled_goal_still_holds_is_taken_by_a_new_goal_with_its_label(world):
    """Nothing moves an issue held by a goal that has ended, so a new goal with its label takes it."""
    client, _, jira, tasks, _ = world
    first = _goal(client, start=True)
    ref = jira.add_key(f"VL-{1_000_000 + uuid.uuid4().int % 10**12}", [first["label"]])
    client.post(f"/v1/goals/{first['id']}/sweep", headers=OPERATOR)
    assert client.post(f"/v1/goals/{first['id']}/cancel", headers=OPERATOR).status_code == 200

    held = lambda goal: [i["ref"] for i in client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["issues"]]  # noqa: E731
    # Creating a started goal sweeps it; the sweep asked for here waits that one out.
    second = _goal(client, label=first["label"], start=True)
    swept = client.post(f"/v1/goals/{second['id']}/sweep", headers=OPERATOR).json()
    assert held(second) == [ref], swept

    # An open goal's issue stays that goal's.
    third = _goal(client, label=first["label"], start=True)
    client.post(f"/v1/goals/{third['id']}/sweep", headers=OPERATOR)
    assert held(third) == [] and held(second) == [ref]


def test_a_goal_that_asks_takes_an_issue_assigned_to_the_meta_harness_with_no_label(world):
    """Assigning an issue to the meta-harness in Jira hands it over: a goal that asked takes it, label or none."""
    client, _, jira, tasks, _ = world
    asks = {"project": "VL", "assigned": "yes"}
    goal = _goal(client, label=None, tracker_settings=asks, start=True)
    ref = jira.add_key(f"VL-{1_000_000 + uuid.uuid4().int % 10**12}", [])
    other = jira.add_key(f"VL-{1_000_000 + uuid.uuid4().int % 10**12}", [])
    jira.assigned.add(ref)

    client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR)

    # Only the assigned one: the other issue of the project is no one's.
    assert other in jira.issues
    assert [t["external_ref"] for t in tasks.tasks.values() if t["goal_id"] == goal["id"]] == [ref]
    # With neither a label, picked issues nor assigned ones, a goal has nothing to take in.
    refused = client.post(
        "/v1/goals",
        json={"statement": "Fix bugs", "repos": [REPO], "tracker_settings": {"project": "VL"}},
        headers=OPERATOR,
    )
    assert refused.status_code == 422 and "assigned to the meta-harness" in refused.json()["detail"]


def test_an_issue_that_is_not_started_is_told_why_in_its_tracker_once_for_each_reason(world):
    """A person who handed an issue over reads in the tracker why no work started, and is not told twice."""
    client, _, jira, _, _ = world
    other = "davecthomas/ai-api-unified"
    goal = _goal(client, repos=[REPO, other], start=True)
    ref = jira.add_key(f"VL-{1_000_000 + uuid.uuid4().int % 10**12}", [goal["label"]])
    jira.issues[ref].repo = ""

    jira.refuse_comments = True
    failed = client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR).json()
    assert failed["replied"] == [] and "could not tell its tracker why it is not started" in failed["errors"][0]

    jira.refuse_comments = False
    first = client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR).json()
    again = client.post(f"/v1/goals/{goal['id']}/sweep", headers=OPERATOR).json()

    assert first["replied"] == [ref] and again["replied"] == []
    [(_, _, said)] = [told for told in jira.told if told[0] == "comment" and told[1] == ref]
    assert said.startswith("The meta-harness has not started work on this issue: it names none of the goal's repos")
    assert "add a line `Repo: <owner/name>`" in said and "its goal's page in the meta-harness console" in said
