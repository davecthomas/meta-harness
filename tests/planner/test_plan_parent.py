# Created by the meta-harness in run t-6dd689b7ea81:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""A Jira goal's `parent`: approving its plan creates each issue under it, against Jira's REST API as it answers.

The approval (planning.approve) runs with its database calls kept in memory, and the Jira adapter talks to a
mock of Jira. No model, no Jira and no GitHub is called.
"""

import contextlib
import json

import httpx
import pytest

from meta_harness.planner import planning, plans, store
from meta_harness.planner.tracker import Issue
from meta_harness.trackers import Source
from meta_harness.trackers.jira import JiraTracker, Site

from .test_planner import Events

REPO = "davecthomas/meta-harness"
EPIC = "jira:VL-100"
BODY = "Build {}.\n\nPaths: console\n\n## Acceptance checks\n\n- make test\n"


def goal(**settings: str) -> dict:
    return {
        "id": "g-plan-parent",
        "label": "meta-harness:goal-plan-parent",
        "repos": [REPO],
        "tracker": "jira",
        "tracker_settings": {"project": "VL", **settings},
        "created_by": "dave",
    }


def draft(draft_id: str, depends_on: tuple = ()) -> dict:
    return plans.clean(
        {
            "draft_id": draft_id,
            "repo": REPO,
            "title": f"Build {draft_id}",
            "body": BODY.format(draft_id),
            "depends_on": list(depends_on),
        }
    )


class MockJira:
    """Jira as the adapter calls it: each issue it creates, and each one read back with its parent.
    refuse_parent: answer a create that names a parent as Jira does a parent it will not take."""

    def __init__(self, refuse_parent: bool = False) -> None:
        self.refuse_parent = refuse_parent
        self.created: list[dict] = []
        self.issues: dict[str, dict] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/rest/api/2")
        if request.method == "POST" and path == "/issue":
            fields = json.loads(request.content)["fields"]
            self.created.append(fields)
            if self.refuse_parent and "parent" in fields:
                return httpx.Response(400, json={"errorMessages": [], "errors": {"parent": "Could not find issue"}})
            key = f"VL-{len(self.issues) + 1}"
            status = {"statusCategory": {"key": "new"}}
            kept = {k: fields[k] for k in ("summary", "description", "labels", "parent") if k in fields}
            self.issues[key] = {"key": key, "fields": {**kept, "status": status}}
            return httpx.Response(201, json={"key": key})
        if request.method == "GET" and path.startswith("/issue/") and path.count("/") == 2:
            return httpx.Response(200, json=self.issues[path.rsplit("/", 1)[1]])
        return httpx.Response(200, json={})


@pytest.fixture
def approve(monkeypatch):
    """planning.approve over a plan of the given drafts, with what it stores kept in memory. Returns the plan it
    approved and the events it recorded."""
    noted: list[tuple[str, str]] = []
    monkeypatch.setattr(planning.db, "connect", lambda **kw: contextlib.nullcontext(None))
    monkeypatch.setattr(
        plans, "record_created", lambda conn, goal_id, version, draft_id, ref: noted.append((draft_id, ref))
    )
    monkeypatch.setattr(plans, "set_tracked", lambda conn, goal_id, version, tracked: None)
    monkeypatch.setattr(store, "add_refs", lambda conn, goal_id, refs: None)
    monkeypatch.setattr(store, "plan_keep", lambda conn, goal_id, ref, repo, waits: None)

    async def run(the_goal: dict, drafts: list[dict], writer, tracked: bool = True) -> tuple[dict, Events]:
        plan = {
            "goal_id": the_goal["id"],
            "version": 1,
            "drafts": drafts,
            "invalid": [],
            "created": {},
            "items": [],
            "issues": [],
            "task_id": "t-plan",
        }
        monkeypatch.setattr(plans, "latest", lambda conn, goal_id: plan)
        events = Events()
        approved = await planning.approve(the_goal, writer, events, "dave", tracked=tracked)
        assert [ref for _, ref in noted] == list(approved["created"].values())
        noted.clear()
        return approved, events

    return run


def jira_for(the_goal: dict, jira: MockJira) -> JiraTracker:
    site = Site(site="https://example.atlassian.net")
    adapter = JiraTracker("bot@example.com", "t", site=lambda: site, transport=httpx.MockTransport(jira))
    return adapter.bound(Source(repos=tuple(the_goal["repos"]), settings=the_goal["tracker_settings"]))


async def test_a_plans_issues_are_created_under_the_parent_the_goal_names(approve):
    jira = MockJira()
    the_goal = goal(parent=EPIC)

    approved, events = await approve(the_goal, [draft("api", ("store",)), draft("store")], jira_for(the_goal, jira))

    assert approved["created"] == {"store": "jira:VL-1", "api": "jira:VL-2"}
    assert [f["summary"] for f in jira.created] == ["Build store", "Build api"], "in dependency order"
    assert [f["parent"] for f in jira.created] == [{"key": "VL-100"}, {"key": "VL-100"}]
    assert "issue_parent_refused" not in events.types()
    assert "plan_approved" in events.types()


async def test_a_parent_jira_refuses_is_reported_and_the_issue_is_still_created(approve):
    jira = MockJira(refuse_parent=True)
    the_goal = goal(parent=EPIC)

    approved, events = await approve(the_goal, [draft("store")], jira_for(the_goal, jira))

    assert approved["created"] == {"store": "jira:VL-1"}, "the approval goes on"
    assert ["parent" in f for f in jira.created] == [True, False], "tried under the parent once, then without it"
    assert "parent" not in jira.issues["VL-1"]["fields"]
    [refused] = [p for t, p in events.events if t == "issue_parent_refused"]
    assert (refused["ref"], refused["parent"], refused["from_ref"], refused["goal_id"]) == (
        "jira:VL-1",
        EPIC,
        "",
        the_goal["id"],
    )
    assert f"Set its parent to {EPIC} in the tracker" in refused["action"]
    assert "plan_approved" in events.types()


async def test_a_goal_that_names_no_parent_creates_its_issues_with_none(approve):
    jira = MockJira()
    the_goal = goal()

    await approve(the_goal, [draft("store")], jira_for(the_goal, jira))

    assert ["parent" in f for f in jira.created] == [False]


class Writer:
    """A tracker with no parents, such as GitHub or the own tracker: the keyword arguments of each create."""

    scheme = "github"

    def __init__(self) -> None:
        self.asked: list[dict] = []

    async def ensure_label(self, repo, label):
        return None

    async def create_issue(self, repo, title, body, labels, **kwargs):
        self.asked.append(kwargs)
        n = len(self.asked)
        return Issue(ref=f"github:{repo}#{n}", repo=repo, number=n, title=title, body=body, labels=labels, state="open")


async def test_a_plan_kept_in_the_meta_harness_is_asked_for_no_parent(approve):
    writer = Writer()

    _, events = await approve(goal(parent=EPIC), [draft("store")], writer, tracked=False)

    assert writer.asked == [{}], "only the goal's own tracker is given its parent"
    assert "issue_parent_refused" not in events.types()


async def test_a_github_goal_is_asked_for_no_parent(approve):
    writer = Writer()
    github = {**goal(), "tracker": "github", "tracker_settings": {}}

    await approve(github, [draft("store")], writer)

    assert writer.asked == [{}]
