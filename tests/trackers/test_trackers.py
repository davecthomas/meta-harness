"""Issue trackers behind one interface: the GitHub adapter's calls, and the router that picks an adapter."""

import pytest

from meta_harness.github_app import GitHubAppError
from meta_harness.trackers import DONE, IN_PROGRESS, TODO, GitHubTracker, Tracker, TrackerError, Trackers
from meta_harness.trackers import registry
from meta_harness.trackers.base import scheme_of

REF = "github:o/r#7"


class Apps:
    """GitHub as the adapter calls it: what it was asked, and what it answers by method and path."""

    def __init__(self, answers: dict | None = None) -> None:
        self.calls: list[tuple] = []
        self.answers = answers or {}

    async def call(self, repo, permissions, method, path, **kwargs):
        self.calls.append((permissions, method, path, kwargs.get("json")))
        answer = self.answers.get((method, path), {})
        if isinstance(answer, Exception):
            raise answer
        return answer


ISSUE = {"number": 7, "title": "Fix it", "body": "Depends on: #3", "state": "open", "labels": [{"name": "mh"}]}


async def test_the_github_adapter_comments_on_an_issue_and_says_where():
    apps = Apps({("POST", "/repos/o/r/issues/7/comments"): {"html_url": "https://github.com/o/r/issues/7#c1"}})

    assert await GitHubTracker(apps).comment(REF, "The task started.") == "https://github.com/o/r/issues/7#c1"
    assert apps.calls == [({"issues": "write"}, "POST", "/repos/o/r/issues/7/comments", {"body": "The task started."})]


async def test_done_closes_a_github_issue_once_and_a_status_it_has_no_state_for_changes_nothing():
    apps = Apps({("GET", "/repos/o/r/issues/7"): ISSUE})
    tracker = GitHubTracker(apps)

    assert await tracker.set_status(REF, IN_PROGRESS) is False and apps.calls == []
    assert await tracker.set_status(REF, DONE) is True
    assert apps.calls[-1] == (
        {"issues": "write"}, "PATCH", "/repos/o/r/issues/7", {"state": "closed", "state_reason": "completed"}
    )  # fmt: skip
    assert await tracker.set_status(REF, TODO) is False, "it is open already"

    apps.answers[("GET", "/repos/o/r/issues/7")] = {**ISSUE, "state": "closed"}
    writes = len([c for c in apps.calls if c[1] == "PATCH"])
    assert await tracker.set_status(REF, DONE) is False
    assert len([c for c in apps.calls if c[1] == "PATCH"]) == writes, "an issue closed already is not closed again"


async def test_the_github_adapter_reads_an_issue_and_knows_its_address():
    tracker = GitHubTracker(Apps({("GET", "/repos/o/r/issues/7"): ISSUE}))

    issue = await tracker.read(REF)

    assert (issue.ref, issue.repo, issue.labels, issue.depends_on) == (REF, "o/r", ["mh"], ["github:o/r#3"])
    assert tracker.url(REF) == "https://github.com/o/r/issues/7"
    assert tracker.url("jira:PROJ-1") is None
    await tracker.link_pull_request(REF, "https://github.com/o/r/pull/9", "Fix it")


async def test_a_github_failure_reaches_the_caller_as_a_tracker_error():
    """No caller needs to know which tracker it is talking to, to know a call failed and whether to retry."""
    refused = GitHubAppError("403 Resource not accessible by integration", retryable=False, status=403)
    tracker = GitHubTracker(Apps({("GET", "/repos/o/r/issues/7"): refused}))

    with pytest.raises(TrackerError, match="403 Resource not accessible") as failed:
        await tracker.read(REF)
    assert (failed.value.retryable, failed.value.status) == (False, 403)

    # A label that is there already is not a failure.
    there = Apps({("POST", "/repos/o/r/labels"): GitHubAppError("422 already_exists", retryable=False, status=422)})
    await GitHubTracker(there).ensure_label("o/r", "mh")


class Other:
    """A second tracker, to show the router picks by the repo's tracker and by the ref's scheme."""

    scheme = "other"

    def __init__(self) -> None:
        self.asked: list[tuple] = []

    async def list_open(self, repo, label):
        self.asked.append(("list_open", repo, label))
        return []

    async def read(self, ref):
        self.asked.append(("read", ref))

    async def ensure_label(self, repo, label):
        self.asked.append(("ensure_label", repo, label))

    async def create_issue(self, repo, title, body, labels):
        self.asked.append(("create_issue", repo, title))

    async def comment(self, ref, body):
        self.asked.append(("comment", ref, body))
        return ""

    async def set_status(self, ref, status):
        self.asked.append(("set_status", ref, status))
        return True

    async def link_pull_request(self, ref, pr_url, title):
        self.asked.append(("link_pull_request", ref, pr_url))

    def url(self, ref):
        return f"https://other.example.com/{ref.removeprefix('other:')}"

    def repo_of(self, ref):
        return "o/elsewhere"


async def test_the_router_picks_by_ref_by_goal_and_else_the_default():
    apps, other = Apps({("GET", "/repos/o/r/issues/7"): ISSUE}), Other()
    github: Tracker = GitHubTracker(apps)
    trackers = Trackers([github, other], default=lambda: "other")

    # A call that names an issue goes to the tracker its ref names.
    await trackers.read("other:K-1")
    await trackers.comment("other:K-1", "started")
    assert await trackers.set_status("other:K-1", DONE) is True
    await trackers.link_pull_request("other:K-1", "https://github.com/o/elsewhere/pull/2", "t")
    assert (await trackers.read(REF)).ref == REF
    # A goal uses one tracker; a goal from before goals named one uses GitHub.
    assert trackers.for_goal({"id": "g-1", "tracker": "other"}) is other
    assert trackers.for_goal({"id": "g-0", "tracker": None}) is github
    # With neither, the default tracker.
    await trackers.list_open("o/r", "mh")
    await trackers.ensure_label("o/r", "mh")
    await trackers.create_issue("o/r", "Split 1", "", ["mh"])
    assert [call[0] for call in other.asked] == [
        "read", "comment", "set_status", "link_pull_request", "list_open", "ensure_label", "create_issue"
    ]  # fmt: skip
    assert [call[1:3] for call in apps.calls] == [("GET", "/repos/o/r/issues/7")]

    assert trackers.url(REF) == "https://github.com/o/r/issues/7"
    assert trackers.url("other:K-1") == "https://other.example.com/K-1"
    assert trackers.url("nowhere:1") is None
    assert trackers.repo_of(REF) == "o/r"
    assert trackers.adapter("github") is github and scheme_of("plain") == ""


async def test_a_tracker_the_meta_harness_does_not_support_is_named_with_what_to_do():
    trackers = Trackers([GitHubTracker(Apps())], default=lambda: "linear")

    for call in (trackers.list_open("o/r", None), trackers.read("linear:ENG-1"), trackers.comment("smoke:1", "x")):
        with pytest.raises(TrackerError, match="does not support \\(supported: github\\)") as refused:
            await call
        assert not refused.value.retryable and "Configuration, Issue trackers" in str(refused.value)
    with pytest.raises(TrackerError, match="goal g-9 names the tracker 'linear'"):
        trackers.for_goal({"id": "g-9", "tracker": "linear"})


def test_the_registry_is_each_adapters_own_description_and_checks_a_configuration():
    """Supported trackers are code, one adapter module each; which are set up and the default are configuration."""
    assert list(registry.SUPPORTED) == ["github", "jira"]
    jira = registry.SUPPORTED["jira"]
    assert [s.name for s in jira.settings] == ["site", "projects", "issue_type", "statuses"]
    assert jira.credentials == ["MH_JIRA_EMAIL", "MH_JIRA_TOKEN"] and jira.writes_progress
    assert not registry.SUPPORTED["github"].writes_progress
    assert registry.check(registry.seed(), None) == [] and registry.seed()["default"] == "github"

    good = {
        "default": "jira",
        "trackers": {
            "github": {},
            "jira": {"site": "https://example.atlassian.net", "projects": [{"key": "VL", "repo": "o/r"}]},
        },
    }
    repos = {"repos": {"repos": {"o/r": {}}}}
    assert registry.check(good, repos) == []
    assert registry.check(good, {"repos": {"repos": {}}}) == [
        "jira: project VL's repo o/r is not in the repo registry; add it there first"
    ]
    assert registry.check({"default": "jira", "trackers": {"github": {}}}, repos) == [
        "default must be one of the trackers set up (github), not 'jira'"
    ]
    bad = {
        "default": "jira",
        "trackers": {
            "linear": {},
            "jira": {
                "site": "http://example.atlassian.net",
                "projects": [{"key": "vl", "repo": "o/r"}, {"key": "AB", "repo": "o/r"}, {"key": "AB", "repo": "o/q"}],
                "statuses": {"shipped": "Done"},
            },
        },
    }
    reasons = registry.check(bad, None)
    assert reasons[0].startswith("linear: not a supported tracker; supported: github, jira")
    assert any("site is the Jira site's address" in r for r in reasons)
    assert any("'vl' is not a project key" in r for r in reasons)
    assert any("project AB is listed for o/r and o/q; one project goes to one repo" in r for r in reasons)
    assert any(
        "statuses names the projects' status for any of todo, in_progress, in_review, done" in r for r in reasons
    )
