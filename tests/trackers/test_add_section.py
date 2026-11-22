# Created by the meta-harness in run t-f1be6babbb70:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""Adding a section to an issue's description, in each adapter: appended, the rest kept, written once."""

import json
from datetime import datetime, timezone

import httpx
import pytest

from meta_harness import outbound
from meta_harness.trackers import GitHubTracker, OwnTracker, Source, TrackerError, Trackers
from meta_harness.trackers.jira import JiraTracker, Site
from meta_harness.trackers.router import WithOwn

HEADING, TEXT = outbound.trusted("Acceptance checks"), outbound.trusted("- `uv run pytest -q`")


class Apps:
    """GitHub as the adapter calls it: what it was asked, and what it answers by method and path."""

    def __init__(self, answers: dict) -> None:
        self.calls: list[tuple] = []
        self.answers = answers

    async def call(self, repo, permissions, method, path, **kwargs):
        self.calls.append((method, path, kwargs.get("json")))
        return self.answers.get((method, path), {})


GITHUB_BODY = "Fix the store.\n\nPaths: src/store\n"


async def test_the_github_adapter_appends_the_section_after_labeling_the_issue():
    apps = Apps({("GET", "/repos/o/r/issues/7"): {"number": 7, "body": GITHUB_BODY}})

    await GitHubTracker(apps, label=lambda: "bot").add_section("github:o/r#7", HEADING, TEXT)

    assert [call[:2] for call in apps.calls] == [
        ("GET", "/repos/o/r/issues/7"),
        ("POST", "/repos/o/r/issues/7/labels"),
        ("PATCH", "/repos/o/r/issues/7"),
    ]
    written = apps.calls[-1][2]["body"]
    assert written == f"{GITHUB_BODY}\n## Acceptance checks\n\n- `uv run pytest -q`\n"
    assert written.startswith(GITHUB_BODY), "the rest of the description is unchanged"


async def test_an_empty_github_body_becomes_the_section_alone():
    apps = Apps({("GET", "/repos/o/r/issues/7"): {"number": 7, "body": None}})

    await GitHubTracker(apps, label=lambda: "bot").add_section("github:o/r#7", HEADING, TEXT)

    assert apps.calls[-1][2] == {"body": "## Acceptance checks\n\n- `uv run pytest -q`\n"}


class Jira:
    """Jira's REST API as the adapter calls it, through a mock transport."""

    def __init__(self, description: str) -> None:
        self.asked: list[tuple[str, str, dict | None]] = []
        self.description = description

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.asked.append((request.method, request.url.path, body))
        if request.method == "GET":
            return httpx.Response(200, json={"key": "VL-7", "fields": {"description": self.description}})
        return httpx.Response(204)


def jira_tracker(jira: Jira) -> JiraTracker:
    adapter = JiraTracker(
        email="bot@example.com",
        token="t",
        site=lambda: Site(site="https://example.atlassian.net"),
        transport=httpx.MockTransport(jira),
        label=lambda: "bot",
    )
    return adapter.bound(Source(repos=("o/r",), settings={"project": "VL"}))


JIRA_DESCRIPTION = "Make it so.\n\nh2. Context\nThe store is slow.\n\nPaths: console"


async def test_the_jira_adapter_appends_the_section_as_a_jira_heading():
    jira = Jira(JIRA_DESCRIPTION)

    await jira_tracker(jira).add_section("jira:VL-7", HEADING, TEXT)

    assert [asked[:2] for asked in jira.asked] == [
        ("GET", "/rest/api/2/issue/VL-7"),
        ("PUT", "/rest/api/2/issue/VL-7"),
        ("PUT", "/rest/api/2/issue/VL-7"),
    ]
    assert jira.asked[1][2] == {"update": {"labels": [{"add": "bot"}]}}, "labeled before the write"
    written = jira.asked[2][2]["fields"]["description"]
    assert written == f"{JIRA_DESCRIPTION}\n\nh2. Acceptance checks\n\n- `uv run pytest -q`\n"
    assert written.startswith(JIRA_DESCRIPTION), "the rest of the description is unchanged"


async def test_a_jira_heading_already_there_is_refused_and_nothing_is_written():
    jira = Jira(f"{JIRA_DESCRIPTION}\n\nh2. acceptance  checks\n* make test")

    with pytest.raises(TrackerError, match="already has a section 'Acceptance checks'") as refused:
        await jira_tracker(jira).add_section("jira:VL-7", HEADING, TEXT)

    assert not refused.value.retryable
    assert [asked[0] for asked in jira.asked] == ["GET"], "neither labeled nor written"


class Cursor:
    def __init__(self, db: "Rows") -> None:
        self.db, self.found, self.description = db, [], None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query: str, params: tuple) -> None:
        self.db.queries.append((query, params))
        row = self.db.row
        if query.startswith("SELECT"):
            self.found = [dict(row)] if params[0] == row["id"] else []
        elif query.startswith("UPDATE"):
            body, issue_id, was = params
            self.found = []
            if issue_id == row["id"] and (row["body"] or "") == was:
                row["body"] = body
                self.found = [{"id": issue_id}]
        self.description = ("id",)

    def fetchall(self):
        return self.found


class Rows:
    """planner.own_issue as one row, behind the connection the own adapter opens: no Postgres needed."""

    def __init__(self, body: str) -> None:
        self.queries: list[tuple[str, tuple]] = []
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.row = {
            "id": "i-0123456789ab",
            "number": 1,
            "repo": "o/r",
            "title": "The store",
            "body": body,
            "labels": [],
            "state": "open",
            "status": "todo",
            "pr_url": None,
            "updated_at": now,
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


async def test_the_own_adapter_appends_the_section_to_the_kept_body():
    rows = Rows(GITHUB_BODY)

    await OwnTracker(connect=rows).add_section(OWN_REF, HEADING, TEXT)

    assert rows.row["body"] == f"{GITHUB_BODY}\n## Acceptance checks\n\n- `uv run pytest -q`\n"
    update = [params for query, params in rows.queries if query.startswith("UPDATE")]
    assert update and update[0][2] == GITHUB_BODY, "written only over the body that was read"


async def test_a_heading_already_there_is_refused_and_nothing_is_written():
    body = f"{GITHUB_BODY}\n## Acceptance checks\n\n- make test\n"
    rows = Rows(body)

    with pytest.raises(TrackerError, match="already has a section") as refused:
        await OwnTracker(connect=rows).add_section(OWN_REF, HEADING, TEXT)

    assert not refused.value.retryable and rows.row["body"] == body
    assert not [query for query, _ in rows.queries if query.startswith("UPDATE")]

    apps = Apps({("GET", "/repos/o/r/issues/7"): {"number": 7, "body": body}})
    with pytest.raises(TrackerError, match="already has a section") as refused:
        await GitHubTracker(apps, label=lambda: "bot").add_section("github:o/r#7", HEADING, TEXT)
    assert not refused.value.retryable and [call[0] for call in apps.calls] == ["GET"]


async def test_a_heading_of_more_than_one_line_is_refused():
    apps = Apps({("GET", "/repos/o/r/issues/7"): {"number": 7, "body": ""}})

    with pytest.raises(TrackerError, match="one line") as refused:
        await GitHubTracker(apps).add_section("github:o/r#7", outbound.trusted("Checks\n\nmore"), TEXT)

    assert not refused.value.retryable and [call[0] for call in apps.calls] == ["GET"]


async def test_a_plain_string_is_refused_before_any_call():
    apps, jira, rows = Apps({}), Jira(""), Rows("")
    adapters = [GitHubTracker(apps), jira_tracker(jira), OwnTracker(connect=rows)]
    refs = ["github:o/r#7", "jira:VL-7", OWN_REF]

    for adapter, ref in zip(adapters, refs):
        with pytest.raises(TypeError, match="must come through the outbound pipeline"):
            await adapter.add_section(ref, HEADING, "- plain text")
        with pytest.raises(TypeError, match="must come through the outbound pipeline"):
            await adapter.add_section(ref, "Acceptance checks", TEXT)

    assert apps.calls == [] and jira.asked == [] and rows.queries == []


async def test_a_text_prepared_for_another_tracker_is_refused():
    for_jira = outbound.Checked("- make test", context=outbound.Context(outbound.JIRA, outbound.BODY))

    with pytest.raises(TypeError, match="was prepared for jira"):
        await GitHubTracker(Apps({})).add_section("github:o/r#7", HEADING, for_jira)


class Recorder:
    """A tracker that records each section it is asked to add."""

    def __init__(self, scheme: str, writes_progress: bool = False) -> None:
        self.scheme, self.writes_progress = scheme, writes_progress
        self.added: list[tuple[str, str, str]] = []

    def bound(self, source):
        return self

    async def add_section(self, ref, heading, text):
        self.added.append((ref, heading, text))


async def test_the_router_sends_a_section_to_the_tracker_the_ref_names():
    github, own = Recorder("github"), Recorder("mh", writes_progress=True)
    trackers = Trackers([github, own], default=lambda: "github")
    goal = trackers.for_goal({"id": "g-1", "tracker": "github", "repos": ["o/r"]})

    await trackers.add_section("github:o/r#7", HEADING, TEXT)
    assert isinstance(goal, WithOwn)
    # A tracker that shows progress by itself still takes a section: it is the issue's content.
    await goal.add_section("github:o/r#8", HEADING, TEXT)
    await goal.add_section(OWN_REF, HEADING, TEXT)

    assert [added[0] for added in github.added] == ["github:o/r#7", "github:o/r#8"]
    assert own.added == [(OWN_REF, HEADING, TEXT)]
