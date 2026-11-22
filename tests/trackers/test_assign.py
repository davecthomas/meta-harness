# Created by the meta-harness in run t-dc988093ef68:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""Each adapter sets an issue's assignee, refuses a person it does not know, and lists its people for the goal
setting `assignee`, against a fake HTTP service or a fake database."""

import json
from datetime import datetime, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from meta_harness.github_app import AppConfig, GitHubApps
from meta_harness.trackers import GitHubTracker, OwnTracker, Source, TrackerError, Trackers
from meta_harness.trackers import github as github_module, jira as jira_module, own as own_module
from meta_harness.trackers.jira import JiraTracker, Site
from meta_harness.trackers.router import WithOwn

SITE = "https://example.atlassian.net"


class Service:
    """A fake HTTP service: what it was asked, with each query, and what it answers by method and path."""

    def __init__(self, answers: dict) -> None:
        self.asked: list[tuple[str, str, dict, dict | None]] = []
        self.answers = answers

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        query = dict(request.url.params)
        self.asked.append((request.method, request.url.path, query, body))
        answer = self.answers.get((request.method, request.url.path))
        if callable(answer):
            answer = answer(query, body)
        if answer is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)

    def sent(self, method: str, path: str) -> list[dict | None]:
        return [body for m, p, _, body in self.asked if (m, p) == (method, path)]

    def writes(self) -> list[tuple[str, str]]:
        return [(m, p) for m, p, _, _ in self.asked if m in ("PUT", "PATCH") or (m == "POST" and "/app/" not in p)]


@pytest.fixture
def key_file(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "app.pem"
    path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return path


def github(key_file, answers: dict) -> tuple[GitHubTracker, Service]:
    token = {"token": "tok", "expires_at": "2099-01-01T00:00:00Z"}
    service = Service(
        {
            ("GET", "/repos/o/r/installation"): {"id": 42},
            ("GET", "/repos/o/s/installation"): {"id": 42},
            ("POST", "/app/installations/42/access_tokens"): httpx.Response(201, json=token),
            **answers,
        }
    )
    apps = GitHubApps({"o": AppConfig("o", "1", key_file)}, transport=httpx.MockTransport(service))
    return GitHubTracker(apps, label=lambda: "bot"), service


def jira(answers: dict) -> tuple[JiraTracker, Service]:
    service = Service(answers)
    site = Site(site=SITE)
    return JiraTracker("bot@example.com", "t", lambda: site, httpx.MockTransport(service), label=lambda: "bot"), service


def issue(assignees: list[str]) -> dict:
    return {
        "number": 7,
        "title": "Fix it",
        "body": "",
        "state": "open",
        "labels": [],
        "assignees": [{"login": login} for login in assignees],
        "assignee": {"login": assignees[0]} if assignees else None,
    }


async def test_github_assigns_the_issue_to_an_assignable_login_after_labeling_it(key_file):
    tracker, service = github(
        key_file,
        {
            ("GET", "/repos/o/r/assignees/octocat"): httpx.Response(204),
            ("GET", "/repos/o/r/issues/7"): issue([]),
            ("POST", "/repos/o/r/issues/7/labels"): [],
            ("PATCH", "/repos/o/r/issues/7"): issue(["octocat"]),
        },
    )

    assert await tracker.assignee("github:o/r#7") == ""
    assert await tracker.assign("github:o/r#7", "octocat") is True

    assert service.sent("PATCH", "/repos/o/r/issues/7") == [{"assignees": ["octocat"]}]
    assert service.writes() == [("POST", "/repos/o/r/issues/7/labels"), ("PATCH", "/repos/o/r/issues/7")]


async def test_github_reads_the_assignee_and_leaves_an_issue_assigned_to_the_person_already(key_file):
    tracker, service = github(
        key_file,
        {
            ("GET", "/repos/o/r/assignees/octocat"): httpx.Response(204),
            ("GET", "/repos/o/r/issues/7"): issue(["Octocat"]),
        },
    )

    assert await tracker.assignee("github:o/r#7") == "Octocat"
    assert await tracker.assign("github:o/r#7", "octocat") is False
    assert service.writes() == []


async def test_github_refuses_a_login_the_repo_cannot_assign_and_writes_nothing(key_file):
    tracker, service = github(key_file, {("GET", "/repos/o/r/issues/7"): issue([])})

    with pytest.raises(TrackerError, match="'nobody' is not someone o/r can assign") as refused:
        await tracker.assign("github:o/r#7", "nobody")

    assert refused.value.retryable is False
    assert service.writes() == []


async def test_github_lists_the_users_assignable_in_the_goals_repos(key_file):
    first = [{"login": f"user{n:03d}"} for n in range(100)]
    tracker, _ = github(
        key_file,
        {
            ("GET", "/repos/o/r/assignees"): lambda query, body: first if query["page"] == "1" else [{"login": "zed"}],
            ("GET", "/repos/o/s/assignees"): [{"login": "Zed"}, {"login": "amy"}],
        },
    )

    found = await tracker.choices("assignee", {"repos": "o/r,o/s"})

    assert [c["value"] for c in found] == ["amy", *[f"user{n:03d}" for n in range(100)], "zed"]
    assert await tracker.choices("assignee", {}) == [], "no repo, nobody to list"
    assert await tracker.choices("project", {"repos": "o/r"}) == []


async def test_jira_assigns_the_issue_to_an_assignable_account_after_labeling_it():
    tracker, service = jira(
        {
            ("GET", "/rest/api/2/user/assignable/search"): [{"accountId": "acc-1", "displayName": "Ada"}],
            ("GET", "/rest/api/2/issue/VL-7"): {"key": "VL-7", "fields": {"assignee": None}},
            ("PUT", "/rest/api/2/issue/VL-7"): httpx.Response(204),
            ("PUT", "/rest/api/2/issue/VL-7/assignee"): httpx.Response(204),
        }
    )

    assert await tracker.assign("jira:VL-7", "acc-1") is True

    [check] = [query for m, p, query, _ in service.asked if p.endswith("/user/assignable/search")]
    assert check == {"issueKey": "VL-7", "accountId": "acc-1"}
    assert service.sent("PUT", "/rest/api/2/issue/VL-7") == [{"update": {"labels": [{"add": "bot"}]}}]
    assert service.sent("PUT", "/rest/api/2/issue/VL-7/assignee") == [{"accountId": "acc-1"}]


async def test_jira_reads_the_assignee_and_leaves_an_issue_assigned_to_the_account_already():
    tracker, service = jira(
        {
            ("GET", "/rest/api/2/user/assignable/search"): [{"accountId": "acc-1"}],
            ("GET", "/rest/api/2/issue/VL-7"): {"key": "VL-7", "fields": {"assignee": {"accountId": "acc-1"}}},
        }
    )

    assert await tracker.assignee("jira:VL-7") == "acc-1"
    assert await tracker.assign("jira:VL-7", "acc-1") is False
    assert service.writes() == []


async def test_jira_refuses_an_account_it_cannot_assign_and_writes_nothing():
    tracker, service = jira({("GET", "/rest/api/2/user/assignable/search"): []})

    with pytest.raises(TrackerError, match="'acc-9' is not someone Jira's VL-7 can assign") as refused:
        await tracker.assign("jira:VL-7", "acc-9")

    assert refused.value.retryable is False
    assert service.writes() == []


async def test_jira_lists_the_assignable_users_of_the_project_it_is_asked_about():
    people = [
        {"accountId": "acc-2", "displayName": "Bea", "active": True, "accountType": "atlassian"},
        {"accountId": "acc-1", "displayName": "Ada", "emailAddress": "ada@example.com", "active": True},
        {"accountId": "acc-3", "displayName": "Gone", "active": False},
        {"accountId": "app-1", "displayName": "Automation", "accountType": "app"},
    ]
    tracker, service = jira(
        {("GET", "/rest/api/2/user/assignable/search"): lambda q, b: people if q["startAt"] == "0" else []}
    )

    found = await tracker.choices("assignee", {"project": "VL"})

    assert found == [{"value": "acc-1", "label": "Ada (ada@example.com)"}, {"value": "acc-2", "label": "Bea"}]
    asked = [query for _, p, query, _ in service.asked if p.endswith("/user/assignable/search")]
    assert asked[0] == {"project": "VL", "startAt": "0", "maxResults": "100"}
    assert await tracker.choices("assignee", {}) == [], "no project, nobody to list"
    bound = tracker.bound(Source(("o/r",), {"project": "VL"}))
    assert [c["value"] for c in await bound.choices("assignee")] == ["acc-1", "acc-2"], "the goal's project"


class Cursor:
    def __init__(self, db: "Rows") -> None:
        self.db, self.found, self.description = db, [], ("id",)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query: str, params: tuple) -> None:
        self.db.queries.append((query, params))
        row, users = self.db.row, self.db.users
        if "FROM planner.console_user" in query:
            known = [u for u in users if u["status"] != "deprovisioned"]
            if params:
                known = [u for u in known if u["email"].lower() == params[0].lower()]
            self.found = [{"email": u["email"]} for u in sorted(known, key=lambda u: u["email"].lower())]
        elif query.startswith("SELECT assignee"):
            self.found = [{"assignee": row["assignee"]}] if params[0] == row["id"] else []
        elif query.startswith("SELECT"):
            self.found = [dict(row)] if params[0] == row["id"] else []
        elif query.startswith("UPDATE planner.own_issue SET assignee"):
            email, issue_id, was = params
            self.found = []
            if issue_id == row["id"] and row["assignee"] != was:
                row["assignee"] = email
                self.found = [{"id": issue_id}]

    def fetchall(self):
        return self.found


class Rows:
    """planner.own_issue as one row and planner.console_user, behind the connection the own adapter opens."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple]] = []
        self.users = [
            {"email": "Bea@example.com", "status": "active"},
            {"email": "ada@example.com", "status": "invited"},
            {"email": "gone@example.com", "status": "deprovisioned"},
        ]
        self.row = {
            "id": "i-0123456789ab",
            "number": 1,
            "repo": "o/r",
            "title": "The store",
            "body": "",
            "labels": [],
            "state": "open",
            "status": "todo",
            "pr_url": None,
            "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "assignee": "",
        }

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, row_factory=None) -> Cursor:
        return Cursor(self)


OWN_REF = "mh:i-0123456789ab"


async def test_the_own_tracker_assigns_the_issue_to_a_console_user():
    rows = Rows()
    own = OwnTracker(connect=rows)

    assert await own.assignee(OWN_REF) == ""
    assert await own.assign(OWN_REF, "bea@EXAMPLE.com") is True
    assert rows.row["assignee"] == "Bea@example.com", "kept as the console user's email"
    assert await own.assignee(OWN_REF) == "Bea@example.com"
    assert await own.assign(OWN_REF, "Bea@example.com") is False, "assigned already"


async def test_the_own_tracker_refuses_a_person_who_is_not_a_console_user_and_writes_nothing():
    rows = Rows()
    own = OwnTracker(connect=rows)

    for person in ("stranger@example.com", "gone@example.com"):
        with pytest.raises(TrackerError, match="is not someone the meta-harness can assign") as refused:
            await own.assign(OWN_REF, person)
        assert refused.value.retryable is False
    assert rows.row["assignee"] == ""
    assert not [query for query, _ in rows.queries if query.startswith("UPDATE")]


async def test_the_own_tracker_lists_its_console_users_who_may_sign_in():
    found = await OwnTracker(connect=Rows()).choices("assignee", {})

    assert [c["value"] for c in found] == ["ada@example.com", "Bea@example.com"]
    assert await OwnTracker(connect=Rows()).choices("project") == []


class Recorder:
    """A tracker that records who it is asked to assign."""

    def __init__(self, scheme: str, writes_progress: bool = False) -> None:
        self.scheme, self.writes_progress = scheme, writes_progress
        self.assigned: list[tuple[str, str]] = []

    def bound(self, source):
        return self

    async def assign(self, ref, person):
        self.assigned.append((ref, person))
        return True

    async def assignee(self, ref):
        return f"{self.scheme}-person"


async def test_the_router_sends_an_assignment_to_the_tracker_the_ref_names():
    hub, own = Recorder("github"), Recorder("mh", writes_progress=True)
    trackers = Trackers([hub, own], default=lambda: "github")
    goal = trackers.for_goal({"id": "g-1", "tracker": "github", "repos": ["o/r"]})

    assert isinstance(goal, WithOwn)
    assert await trackers.assign("github:o/r#7", "octocat")
    # A tracker that shows progress by itself still takes an assignee.
    assert await goal.assign("github:o/r#8", "octocat")
    assert await goal.assign(OWN_REF, "ada@example.com")
    assert await goal.assignee(OWN_REF) == "mh-person" and await trackers.assignee("github:o/r#7") == "github-person"

    assert hub.assigned == [("github:o/r#7", "octocat"), ("github:o/r#8", "octocat")]
    assert own.assigned == [(OWN_REF, "ada@example.com")]


@pytest.mark.parametrize("module", [github_module, jira_module, own_module], ids=["github", "jira", "own"])
def test_every_tracker_asks_a_goal_for_an_optional_assignee_picked_from_its_people(module):
    [setting] = [s for s in module.SPEC.goal_settings if s.name == "assignee"]

    assert (setting.kind, setting.required) == ("choice", False)
    assert setting.scope == {"github": ["repos"], "jira": ["project"], "mh": []}[module.SPEC.scheme]
