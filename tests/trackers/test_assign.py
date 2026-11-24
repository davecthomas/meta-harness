# Created by the meta-harness in run t-6ba89f3bc769:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""Each adapter assigns an issue to a person its tracker knows, refuses one it does not, and lists the people a
goal's `assignee` setting is picked from, against a fake HTTP service or a fake database."""

import json
from datetime import datetime, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from meta_harness.github_app import AppConfig, GitHubApps
from meta_harness.trackers import GitHubTracker, OwnTracker, Source, TrackerError, Trackers, registry
from meta_harness.trackers.jira import JiraTracker, Site
from meta_harness.trackers.router import WithOwn

SITE = "https://example.atlassian.net"
ALICE = {"accountId": "acc-alice", "displayName": "Alice Ng", "emailAddress": "alice@example.com", "active": True}
BOB = {"accountId": "acc-bob", "displayName": "Bob Ray", "active": True}
GONE = {"accountId": "acc-gone", "displayName": "Gone", "active": False}


class Service:
    """A fake HTTP service: what it was asked, with each query, and what it answers by method and path. An
    answer may be a function of the request's query and body."""

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
        return [(m, p) for m, p, _, _ in self.asked if m in ("PUT", "PATCH") or p.endswith("/labels")]


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
    adapter = JiraTracker("bot@example.com", "t", lambda: site, httpx.MockTransport(service), label=lambda: "bot")
    return adapter, service


GITHUB_ISSUE = {"number": 7, "title": "Fix it", "body": "", "state": "open", "labels": [], "assignees": []}


async def test_github_assigns_a_repo_assignee_and_reads_it_back(key_file):
    issue = dict(GITHUB_ISSUE)

    def patch(query, body):
        issue["assignees"] = [{"login": login} for login in body["assignees"]]
        return issue

    tracker, service = github(
        key_file,
        {
            ("GET", "/repos/o/r/assignees/octocat"): httpx.Response(204),
            ("GET", "/repos/o/r/issues/7"): lambda query, body: issue,
            ("POST", "/repos/o/r/issues/7/labels"): [],
            ("PATCH", "/repos/o/r/issues/7"): patch,
        },
    )

    assert await tracker.assignee("github:o/r#7") == ""
    assert await tracker.assign("github:o/r#7", "octocat") is True

    assert service.sent("PATCH", "/repos/o/r/issues/7") == [{"assignees": ["octocat"]}]
    assert service.sent("POST", "/repos/o/r/issues/7/labels") == [{"labels": ["bot"]}], "labeled before the write"
    assert await tracker.assignee("github:o/r#7") == "octocat"
    assert await tracker.assign("github:o/r#7", "octocat") is False, "the same person again changes nothing"
    issue["assignees"] = [{"login": "someone"}]
    assert await tracker.assign("github:o/r#7", "octocat") is False, "a person's assignment is kept"
    assert len(service.sent("PATCH", "/repos/o/r/issues/7")) == 1


@pytest.mark.parametrize("person", ["stranger", "not a login", ""])
async def test_github_refuses_a_person_the_repo_cannot_assign_and_writes_nothing(key_file, person):
    tracker, service = github(key_file, {("GET", "/repos/o/r/issues/7"): GITHUB_ISSUE})

    with pytest.raises(TrackerError, match="is not one of the people who can be assigned issues in o/r") as refused:
        await tracker.assign("github:o/r#7", person)

    assert not refused.value.retryable
    assert service.writes() == []


async def test_github_lists_the_people_assignable_in_every_one_of_the_goals_repos(key_file):
    many = [{"login": f"user{n}"} for n in range(100)]
    pages = {"1": many, "2": [{"login": "Zed"}, {"login": "octocat"}]}
    tracker, service = github(
        key_file,
        {
            ("GET", "/repos/o/r/assignees"): lambda query, body: pages[query["page"]],
            ("GET", "/repos/o/s/assignees"): [{"login": "octocat"}, {"login": "zed"}, {"login": "other"}],
        },
    )

    assert await tracker.choices("assignee", {"repo": "o/r"}) == sorted(
        [{"value": u["login"], "label": u["login"]} for u in many + pages["2"]], key=lambda c: c["value"].lower()
    )
    both = await tracker.choices("assignee", {"repos": "o/r,o/s"})
    assert both == [{"value": "octocat", "label": "octocat"}, {"value": "Zed", "label": "Zed"}]
    assert await tracker.choices("assignee", {}) == [] and await tracker.choices("project", {"repo": "o/r"}) == []
    with pytest.raises(TrackerError, match="is not a repo's owner/name") as refused:
        await tracker.choices("assignee", {"repo": "o/r/extra"})
    assert not refused.value.retryable


def jira_assignable(query: dict, body) -> list[dict]:
    """Jira's assignable-user search for one issue, by account id or by a part of a name."""
    people = [ALICE, BOB, GONE]
    if "accountId" in query:
        if not query["accountId"].startswith("acc-"):
            return httpx.Response(400, json={"errorMessages": ["The account id is not valid."]})
        return [p for p in people if p["accountId"] == query["accountId"]]
    return [p for p in people if query.get("query", "").lower() in p["displayName"].lower()]


async def test_jira_assigns_an_assignable_person_by_account_id_or_display_name():
    assignee: dict = {"value": None}

    def put(query, body):
        assignee["value"] = body
        return httpx.Response(204)

    tracker, service = jira(
        {
            ("GET", "/rest/api/2/user/assignable/search"): jira_assignable,
            ("GET", "/rest/api/2/issue/VL-7"): lambda query, body: {
                "key": "VL-7",
                "fields": {"assignee": assignee["value"]},
            },
            ("GET", "/rest/api/2/issue/VL-8"): {"key": "VL-8", "fields": {"assignee": None}},
            ("PUT", "/rest/api/2/issue/VL-7"): httpx.Response(204),
            ("PUT", "/rest/api/2/issue/VL-8"): httpx.Response(204),
            ("PUT", "/rest/api/2/issue/VL-7/assignee"): put,
            ("PUT", "/rest/api/2/issue/VL-8/assignee"): httpx.Response(204),
        }
    )
    bound = tracker.bound(Source(repos=("o/r",), settings={"project": "VL"}))

    assert await bound.assignee("jira:VL-7") == ""
    assert await bound.assign("jira:VL-7", "acc-alice") is True
    assert service.sent("PUT", "/rest/api/2/issue/VL-7/assignee") == [{"accountId": "acc-alice"}]
    assert service.sent("PUT", "/rest/api/2/issue/VL-7") == [{"update": {"labels": [{"add": "bot"}]}}]
    assert await bound.assignee("jira:VL-7") == "acc-alice"
    assert await bound.assign("jira:VL-7", "acc-alice") is False
    assert await bound.assign("jira:VL-7", "acc-bob") is False, "a person's assignment is kept"
    assert len(service.sent("PUT", "/rest/api/2/issue/VL-7/assignee")) == 1

    assert await bound.assign("jira:VL-8", "bob ray") is True, "a display name that names one person"
    assert service.sent("PUT", "/rest/api/2/issue/VL-8/assignee") == [{"accountId": "acc-bob"}]
    searches = [(p, query) for m, p, query, _ in service.asked if p == "/rest/api/2/user/assignable/search"]
    assert {query["issueKey"] for _, query in searches} == {"VL-7", "VL-8"}, "matched among who the issue can have"


@pytest.mark.parametrize("person", ["acc-stranger", "Stranger", "Gone", "a", ""])
async def test_jira_refuses_a_person_it_cannot_assign_and_writes_nothing(person):
    tracker, service = jira({("GET", "/rest/api/2/user/assignable/search"): jira_assignable})

    with pytest.raises(TrackerError, match="is not one of the people VL-7 can be assigned to in Jira") as refused:
        await tracker.assign("jira:VL-7", person)

    assert not refused.value.retryable
    assert service.writes() == []


async def test_jira_lists_the_projects_assignable_users_a_page_at_a_time():
    first = [{"accountId": f"acc-{n:03}", "displayName": f"User {n:03}", "active": True} for n in range(99)] + [GONE]
    pages = {"0": first, "100": [ALICE, BOB]}
    tracker, service = jira(
        {("GET", "/rest/api/2/user/assignable/search"): lambda query, body: pages[query["startAt"]]}
    )

    found = await tracker.choices("assignee", {"project": "VL"})

    asked = [query for _, p, query, _ in service.asked if p == "/rest/api/2/user/assignable/search"]
    assert [(q["project"], q["startAt"]) for q in asked] == [("VL", "0"), ("VL", "100")]
    assert found[:2] == [
        {"value": "acc-alice", "label": "Alice Ng (alice@example.com)"},
        {"value": "acc-bob", "label": "Bob Ray"},
    ]
    assert len(found) == 101 and "acc-gone" not in [c["value"] for c in found], "an inactive account is not offered"
    assert await tracker.choices("assignee", {}) == [] == await tracker.choices("assignee", {"project": "x y"})


class Cursor:
    def __init__(self, db: "People") -> None:
        self.db, self.found, self.description = db, [], None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query: str, params: tuple) -> None:
        self.db.queries.append((query, params))
        row, users = self.db.row, self.db.users
        self.description = ("x",)
        if query.startswith("SELECT email FROM planner.console_user WHERE lower"):
            self.found = [
                {"email": e} for e, s in users.items() if e.lower() == params[0].lower() and s != "deprovisioned"
            ]
        elif query.startswith("SELECT email FROM planner.console_user"):
            self.found = [{"email": e} for e, s in sorted(users.items()) if s != "deprovisioned"]
        elif query.startswith("SELECT assignee"):
            self.found = [{"assignee": row["assignee"]}] if params[0] == row["id"] else []
        elif query.startswith("SELECT"):
            self.found = [dict(row)] if params[0] == row["id"] else []
        elif query.startswith("UPDATE planner.own_issue SET assignee"):
            assert "assignee IS NULL" in query, "only an issue with no assignee is assigned"
            email, issue_id = params
            self.found = []
            if issue_id == row["id"] and row["assignee"] is None:
                row["assignee"] = email
                self.found = [{"id": issue_id}]
        else:
            raise AssertionError(f"unexpected query: {query}")

    def fetchall(self):
        return self.found


class People:
    """planner.own_issue as one row and planner.console_user as a few, behind the connection the own adapter
    opens: no Postgres needed."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple]] = []
        self.users = {"ana@example.com": "active", "ben@example.com": "invited", "old@example.com": "deprovisioned"}
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
            "assignee": None,
            "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
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


async def test_the_own_tracker_assigns_a_console_user_and_reads_it_back():
    people = People()
    own = OwnTracker(connect=people)

    assert await own.assignee(OWN_REF) == ""
    assert await own.assign(OWN_REF, "Ana@Example.com") is True
    assert people.row["assignee"] == "ana@example.com", "kept as the console spells the email"
    assert await own.assignee(OWN_REF) == "ana@example.com"
    assert await own.assign(OWN_REF, "ana@example.com") is False
    assert await own.assign(OWN_REF, "ben@example.com") is False, "a person's assignment is kept"
    assert people.row["assignee"] == "ana@example.com"
    assert await own.choices("assignee") == [
        {"value": "ana@example.com", "label": "ana@example.com"},
        {"value": "ben@example.com", "label": "ben@example.com"},
    ]
    assert await own.choices("project") == []


@pytest.mark.parametrize("person", ["stranger@example.com", "old@example.com", ""])
async def test_the_own_tracker_refuses_a_person_who_is_not_a_console_user(person):
    people = People()

    with pytest.raises(TrackerError, match="is not one of the console's users") as refused:
        await OwnTracker(connect=people).assign(OWN_REF, person)

    assert not refused.value.retryable and people.row["assignee"] is None
    assert not [query for query, _ in people.queries if query.startswith("UPDATE")]


async def test_the_own_tracker_says_an_issue_it_does_not_keep_is_not_there():
    own = OwnTracker(connect=People())

    with pytest.raises(TrackerError, match="is not a task Muxxy keeps") as missing:
        await own.assign("mh:i-ffffffffffff", "ana@example.com")
    assert not missing.value.retryable
    with pytest.raises(TrackerError, match="is not mh:i-") as malformed:
        await own.assignee("mh:nope")
    assert not malformed.value.retryable


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
    github, own = Recorder("github"), Recorder("mh", writes_progress=True)
    trackers = Trackers([github, own], default=lambda: "github")
    goal = trackers.for_goal({"id": "g-1", "tracker": "github", "repos": ["o/r"]})

    assert isinstance(goal, WithOwn)
    assert await trackers.assign("github:o/r#7", "octocat")
    # A tracker that shows progress by itself is still assigned: who has the issue is not its progress.
    assert await goal.assign("github:o/r#8", "octocat")
    assert await goal.assign(OWN_REF, "ana@example.com")
    assert await goal.assignee(OWN_REF) == "mh-person" and await trackers.assignee("github:o/r#7") == "github-person"

    assert github.assigned == [("github:o/r#7", "octocat"), ("github:o/r#8", "octocat")]
    assert own.assigned == [(OWN_REF, "ana@example.com")]


def test_every_tracker_asks_a_goal_for_an_optional_assignee_picked_from_its_people():
    for spec in [*registry.SUPPORTED.values(), registry.OWN]:
        [assignee] = [s for s in spec.goal_settings if s.name == "assignee"]
        assert (assignee.kind, assignee.required) == ("choice", False), spec.scheme


# What a refusal tells the operator to do, whichever tracker refuses.
WHAT_TO_DO = "Choose one of those for the goal's assignee, or add the person to the tracker."


async def test_every_adapters_refusal_says_what_to_do(key_file):
    hub, _ = github(key_file, {("GET", "/repos/o/r/issues/7"): GITHUB_ISSUE})
    site, _ = jira({("GET", "/rest/api/2/user/assignable/search"): jira_assignable})
    own = OwnTracker(connect=People())

    for tracker, ref in [(hub, "github:o/r#7"), (site, "jira:VL-7"), (own, OWN_REF)]:
        with pytest.raises(TrackerError) as refused:
            await tracker.assign(ref, "stranger")
        assert str(refused.value).startswith(f"{ref} was not assigned: 'stranger' is not one of ")
        assert str(refused.value).endswith(WHAT_TO_DO), tracker.scheme


async def test_github_lists_the_people_of_one_repo(key_file):
    tracker, _ = github(key_file, {("GET", "/repos/o/r/assignees"): [{"login": "zed"}, {"login": "Octocat"}]})

    assert await tracker.people("o/r") == [
        {"value": "Octocat", "label": "Octocat"},
        {"value": "zed", "label": "zed"},
    ]
    with pytest.raises(TrackerError, match="is not a repo's owner/name") as refused:
        await tracker.people("not a repo")
    assert not refused.value.retryable


async def test_jira_lists_the_people_of_the_bound_goals_project_and_none_without_one():
    tracker, service = jira({("GET", "/rest/api/2/user/assignable/search"): [ALICE, BOB, GONE]})

    bound = tracker.bound(Source(repos=("o/r", "o/s"), settings={"project": "VL"}))
    people = [
        {"value": "acc-alice", "label": "Alice Ng (alice@example.com)"},
        {"value": "acc-bob", "label": "Bob Ray"},
    ]
    assert await bound.people("o/r") == people == await bound.people("o/s")
    assert {query["project"] for _, _, query, _ in service.asked} == {"VL"}
    asked = len(service.asked)
    assert await tracker.people("o/r") == []
    assert await tracker.bound(Source(repos=("o/r",), settings={})).people("o/r") == []
    assert len(service.asked) == asked, "no project, nothing asked"


async def test_the_own_tracker_lists_its_console_users_for_any_repo():
    own = OwnTracker(connect=People())

    users = [
        {"value": "ana@example.com", "label": "ana@example.com"},
        {"value": "ben@example.com", "label": "ben@example.com"},
    ]
    assert await own.people("o/r") == users == await own.people("x/y")


@pytest.mark.parametrize(
    ("scheme", "good", "bad"),
    [
        ("github", ["octocat", "a-b-c"], ["not a login", "-x", " octocat", "a" * 40]),
        ("jira", ["acc-alice", "557058:f58131cb-b67d", "Alice Ng"], [" acc-alice", "two\nlines", "x" * 256]),
        ("mh", ["ana@example.com"], ["ana", "ana@example", "a b@example.com"]),
    ],
)
def test_every_tracker_checks_a_goals_assignee_when_it_is_saved(scheme, good, bad):
    spec = registry.spec(scheme)
    base = {"project": "VL"} if scheme == "jira" else {}
    site = {"projects": {"VL": {}}}

    assert spec.check_goal(base, site) == [], "the assignee is optional"
    assert spec.check_goal({**base, "assignee": ""}, site) == []
    for value in good:
        assert spec.check_goal({**base, "assignee": value}, site) == [], value
    for value in bad:
        [why] = spec.check_goal({**base, "assignee": value}, site)
        assert why.startswith("assignee is ") and why.endswith("choose one from the list, or leave it empty"), value


async def test_github_lists_a_repos_open_issues_as_the_ones_a_goal_can_be_planned_from(key_file):
    issues = [
        {
            "number": 4,
            "title": "Data export",
            "body": "",
            "labels": [],
            "state": "open",
            "updated_at": "2026-11-02T00:00:00Z",
        },
        {"number": 9, "title": "Sync", "body": "", "labels": [], "state": "open", "updated_at": "2026-11-20T00:00:00Z"},
        {"number": 5, "title": "A pull request", "pull_request": {}, "updated_at": "2026-11-21T00:00:00Z"},
    ]
    tracker, _ = github(
        key_file, {("GET", "/repos/o/r/issues"): lambda query, body: issues if query["page"] == "1" else []}
    )

    found = await tracker.choices("parent", {"repos": "o/r"})

    assert found == [
        {"value": "github:o/r#9", "label": "#9 Sync"},
        {"value": "github:o/r#4", "label": "#4 Data export"},
    ], "newest first, and no pull request"
    assert await tracker.choices("parent", {}) == []


async def _github_text(title: str, body: str):
    from meta_harness import outbound

    return (
        await outbound.prepare(title, outbound.Context(outbound.GITHUB, outbound.TITLE, outbound.FIXED)),
        await outbound.prepare(body, outbound.Context(outbound.GITHUB, outbound.BODY, outbound.FIXED)),
    )


async def test_a_new_github_issue_becomes_a_sub_issue_of_the_plans_epic(key_file):
    created = {**GITHUB_ISSUE, "id": 9001, "number": 8, "title": "Build the button"}
    tracker, service = github(
        key_file,
        {
            ("POST", "/repos/o/r/issues"): httpx.Response(201, json=created),
            ("POST", "/repos/o/r/issues/7/sub_issues"): httpx.Response(201, json={}),
        },
    )
    title, body = await _github_text("Build the button", "Add it to the account page.")

    made = await tracker.create_issue("o/r", title, body, ["mh:g"], parent="github:o/r#7")

    assert made.parent == "github:o/r#7"
    assert service.sent("POST", "/repos/o/r/issues/7/sub_issues") == [{"sub_issue_id": 9001}], "by id, not number"


async def test_a_parent_github_refuses_leaves_the_new_issue_standing_alone(key_file):
    created = {**GITHUB_ISSUE, "id": 9001, "number": 8, "title": "Build the button"}
    # No answer for the sub-issues call: GitHub answers 404, as where sub-issues are not available.
    tracker, service = github(key_file, {("POST", "/repos/o/r/issues"): httpx.Response(201, json=created)})
    title, body = await _github_text("Build the button", "Add it to the account page.")

    made = await tracker.create_issue("o/r", title, body, ["mh:g"], parent="github:o/r#7")

    assert (made.ref, made.parent) == ("github:o/r#8", None), "the task exists either way"
    alone = await tracker.create_issue("o/r", title, body, ["mh:g"])
    assert alone.parent is None and len(service.sent("POST", "/repos/o/r/issues/7/sub_issues")) == 1
