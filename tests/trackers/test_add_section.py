# Created by the meta-harness in run t-6bac325712f8:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""A section added to an issue's description: appended, the rest unchanged, and never written twice."""

import json

import httpx
import pytest

from meta_harness import outbound
from meta_harness.trackers import GitHubTracker, Source, TrackerError, Trackers
from meta_harness.trackers.base import with_section
from meta_harness.trackers.jira import JiraTracker, Site, markdown
from meta_harness.trackers.own import OwnTracker

REF = "github:o/r#7"
ISSUE = {"number": 7, "title": "Fix it", "body": "", "state": "open", "labels": []}


class Apps:
    """GitHub as the adapter calls it: what it was asked, and what it answers by method and path."""

    def __init__(self, answers: dict | None = None) -> None:
        self.calls: list[tuple] = []
        self.answers = answers or {}

    async def call(self, repo, permissions, method, path, **kwargs):
        self.calls.append((permissions, method, path, kwargs.get("json")))
        return self.answers.get((method, path), {})


class Jira:
    """Jira's REST API as a mock transport: what the adapter asked, and what each method and path answers."""

    def __init__(self, answers: dict | None = None) -> None:
        self.asked: list[tuple[str, str, dict | None]] = []
        self.answers = answers or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.asked.append((request.method, request.url.path, body))
        return httpx.Response(200, json=self.answers.get((request.method, request.url.path), {}))


def tracker(jira: Jira) -> JiraTracker:
    site = Site(site="https://example.atlassian.net")
    adapter = JiraTracker("bot@example.com", "t", site=lambda: site, transport=httpx.MockTransport(jira))
    return adapter.bound(Source(("o/r",), {"project": "VL"}))


def issue(key: str, description: str) -> dict:
    return {"key": key, "fields": {"summary": "Fix it", "description": description, "labels": []}}


BODY = "Fix it.\n\nPaths: src/store\nDepends on: #3"
CHECKS = outbound.trusted("- `uv run pytest -q`\n")


async def test_the_github_adapter_appends_the_section_after_labeling_the_issue():
    apps = Apps({("GET", "/repos/o/r/issues/7"): {**ISSUE, "body": BODY}})

    await GitHubTracker(apps).add_section(REF, "Acceptance checks", CHECKS)

    assert [call[1:3] for call in apps.calls] == [
        ("GET", "/repos/o/r/issues/7"),
        ("POST", "/repos/o/r/issues/7/labels"),
        ("PATCH", "/repos/o/r/issues/7"),
    ]
    written = apps.calls[-1][3]["body"]
    assert written == f"{BODY}\n\n## Acceptance checks\n\n- `uv run pytest -q`\n"
    assert written.startswith(BODY), "the rest of the description is unchanged"


async def test_the_jira_adapter_appends_a_wiki_heading_that_reads_back_as_the_section():
    description = "Make it so.\n\nh2. Paths\nconsole\n"
    jira = Jira({("GET", "/rest/api/2/issue/VL-7"): issue("VL-7", description=description)})

    await tracker(jira).add_section("jira:VL-7", "Acceptance checks", CHECKS)

    assert [(method, path) for method, path, _ in jira.asked] == [
        ("GET", "/rest/api/2/issue/VL-7"),
        ("PUT", "/rest/api/2/issue/VL-7"),
        ("PUT", "/rest/api/2/issue/VL-7"),
    ]
    assert jira.asked[1][2] == {"update": {"labels": [{"add": "ai:meta-harness"}]}}, "labeled first"
    written = jira.asked[2][2]["fields"]["description"]
    assert written == f"{description}\nh2. Acceptance checks\n\n- `uv run pytest -q`\n"
    assert written.startswith(description), "the rest of the description is unchanged"
    assert "## Acceptance checks" in markdown(written)


async def test_the_router_sends_the_section_to_the_tracker_the_ref_names():
    apps = Apps({("GET", "/repos/o/r/issues/7"): {**ISSUE, "body": BODY}})
    trackers = Trackers([GitHubTracker(apps)], default=lambda: "github")

    await trackers.add_section(REF, "Acceptance checks", CHECKS)

    assert apps.calls[-1][1:3] == ("PATCH", "/repos/o/r/issues/7")


async def test_a_plain_string_is_refused_by_every_adapter_before_any_call():
    apps, jira = Apps(), Jira()
    adapters = [
        (GitHubTracker(apps), REF),
        (tracker(jira), "jira:VL-7"),
        (OwnTracker(connect=lambda: pytest.fail("no call is made")), "mh:i-000000000000"),
    ]

    for adapter, ref in adapters:
        with pytest.raises(TypeError, match="must come through the outbound pipeline"):
            await adapter.add_section(ref, "Acceptance checks", "- make test")
    assert apps.calls == [] and jira.asked == []


async def test_a_text_prepared_for_another_tracker_is_refused():
    for_jira = outbound.Checked("- make test", context=outbound.Context(outbound.JIRA))

    with pytest.raises(TypeError, match="prepared for jira"):
        await GitHubTracker(Apps()).add_section(REF, "Acceptance checks", for_jira)


@pytest.mark.parametrize("heading", ["Acceptance checks", "acceptance checks", "  Acceptance checks "])
async def test_a_heading_the_description_has_is_refused_and_nothing_is_written(heading):
    held = f"{BODY}\n\n### Acceptance Checks\n\n- make test\n"
    apps = Apps({("GET", "/repos/o/r/issues/7"): {**ISSUE, "body": held}})
    jira = Jira({("GET", "/rest/api/2/issue/VL-7"): issue("VL-7", description="Do it.\nh2. Acceptance checks\n* x")})

    for adapter, ref, asked in ((GitHubTracker(apps), REF, apps.calls), (tracker(jira), "jira:VL-7", jira.asked)):
        with pytest.raises(TrackerError, match="already has a section headed") as refused:
            await adapter.add_section(ref, heading, CHECKS)
        assert not refused.value.retryable
        assert len(asked) == 1, "only the read: no label and no write"


async def test_a_heading_that_is_empty_or_several_lines_is_refused():
    for heading in ("", "  ", "Acceptance\nchecks"):
        with pytest.raises(ValueError, match="one line"):
            await GitHubTracker(Apps()).add_section(REF, heading, CHECKS)


def test_a_section_goes_after_a_blank_line_and_an_empty_description_is_the_section_alone():
    assert with_section("", "## H", "x") == "## H\n\nx\n"
    assert with_section("a\n", "## H", "x") == "a\n\n## H\n\nx\n"
    assert with_section("a\n\n\n", "## H", "x") == "a\n\n\n## H\n\nx\n"
