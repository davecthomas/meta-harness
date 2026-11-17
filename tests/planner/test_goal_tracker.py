"""A goal uses one issue tracker: the default when it is created unless it names another, and keeps it."""

import uuid

import pytest
from fastapi.testclient import TestClient

from meta_harness.planner.service import create_app
from meta_harness.trackers import Issue, Trackers
from meta_harness.trackers import registry as tracker_registry

from .test_planner import OPERATOR, Events, FakeTasks, FakeTracker, _no_title, migrated, registry  # noqa: F401

pytestmark = pytest.mark.db

REPO = "davecthomas/meta-harness"


class FakeJira(FakeTracker):
    """Jira as the planner sees it: refs are jira:<KEY>, and a project's issues go to one repo."""

    scheme = "jira"

    def add_key(self, key: str, labels: list[str], title: str = "From Jira") -> str:
        ref = f"jira:{key}"
        self.issues[ref] = Issue(
            ref=ref, repo=REPO, number=int(key.split("-")[1]), title=title, body="", labels=labels, state="open",
            checks=["make test"],
        )  # fmt: skip
        return ref

    def repo_of(self, ref: str) -> str:
        if not ref.startswith("jira:VL-"):
            raise ValueError(f"{ref!r} is not jira:<PROJECT>-<number>")
        return REPO


class FakeGitHub(FakeTracker):
    scheme = "github"

    def repo_of(self, ref: str) -> str:
        from meta_harness.trackers.github import parse_ref

        return parse_ref(ref)[0]


@pytest.fixture
def world(monkeypatch):
    """A planner with GitHub and Jira, both set up, and Jira the default."""
    document = {
        "default": "jira",
        "trackers": {"github": {}, "jira": {"site": "https://example.atlassian.net", "projects": [{"key": "VL", "repo": REPO}]}},
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
    made = client.post("/v1/goals", json=body, headers=OPERATOR)
    assert made.status_code == 201, made.text
    return made.json()


def test_a_new_goal_takes_the_default_tracker_and_reads_its_issues_from_it(world):
    client, github, jira, tasks, _ = world
    goal = _goal(client)
    assert goal["tracker"] == "jira"
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
    mixed = client.post(
        "/v1/goals",
        json={"statement": "Mixed", "repos": [REPO], "refs": [f"github:{REPO}#5"], "start": False},
        headers=OPERATOR,
    )
    assert mixed.status_code == 422 and "is not a jira issue, and the goal's tracker is jira" in mixed.text

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
    assert list(by) == ["github", "jira"]
    assert by["github"]["configured"] and by["github"]["signed_in"] and not by["github"]["writes_progress"]
    assert [s["name"] for s in by["jira"]["settings"]] == ["site", "projects", "issue_type", "statuses"]
    assert by["jira"]["configured"] and not by["jira"]["signed_in"]
    assert by["jira"]["missing_credentials"] == ["MH_JIRA_TOKEN"] and by["jira"]["writes_progress"]

    label = f"mh:{uuid.uuid4().hex[:8]}"
    jira.add_key("VL-77", [label])
    github.add(REPO, 78, labels=[label])
    default = client.get(f"/v1/repos/{REPO}/issues", params={"label": label}, headers=OPERATOR).json()
    assert [i["ref"] for i in default["issues"]] == ["jira:VL-77"], "the default tracker's, when none is named"
    named = client.get(f"/v1/repos/{REPO}/issues", params={"label": label, "tracker": "github"}, headers=OPERATOR)
    assert [i["ref"] for i in named.json()["issues"]] == [f"github:{REPO}#78"]
