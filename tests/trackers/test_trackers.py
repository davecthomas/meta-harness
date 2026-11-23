"""Issue trackers behind one interface: the GitHub adapter's calls, and the router that picks an adapter."""

import pytest

from meta_harness.github_app import GitHubAppError
from meta_harness.trackers import DONE, IN_PROGRESS, TODO, GitHubTracker, Tracker, TrackerError, Trackers
from meta_harness.trackers import registry
from meta_harness.trackers.base import scheme_of

pytestmark = pytest.mark.usefixtures("unchecked_text")

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

    tracker = GitHubTracker(apps)
    assert await tracker.comment(REF, "The task started.") == "https://github.com/o/r/issues/7#c1"
    await tracker.comment(REF, "Again.")
    # Labeled as the meta-harness's before its first write, and once for all of them.
    assert apps.calls == [
        ({"issues": "write"}, "POST", "/repos/o/r/issues/7/labels", {"labels": ["ai:meta-harness"]}),
        ({"issues": "write"}, "POST", "/repos/o/r/issues/7/comments", {"body": "The task started."}),
        ({"issues": "write"}, "POST", "/repos/o/r/issues/7/comments", {"body": "Again."}),
    ]


async def test_an_issue_the_github_adapter_creates_or_closes_carries_the_meta_harness_label():
    apps = Apps({("POST", "/repos/o/r/issues"): ISSUE, ("GET", "/repos/o/r/issues/8"): {**ISSUE, "number": 8}})
    tracker = GitHubTracker(apps, label=lambda: "bot")

    made = await tracker.create_issue("o/r", "Fix it", "Body.", ["mh", "bot"])
    assert apps.calls[0][3]["labels"] == ["mh", "bot"], "the label is carried once"
    await tracker.mark(made.ref)
    assert len(apps.calls) == 1, "an issue made with the label is not labeled again"

    assert await tracker.set_status("github:o/r#8", DONE) is True
    assert [call[2] for call in apps.calls[-2:]] == ["/repos/o/r/issues/8/labels", "/repos/o/r/issues/8"]


async def test_the_github_adapter_creates_the_same_issue_when_given_a_parent():
    """The GitHub adapter sets no parent, so an issue given one is created as it always was."""
    plain, under = Apps({("POST", "/repos/o/r/issues"): ISSUE}), Apps({("POST", "/repos/o/r/issues"): ISSUE})

    made = await GitHubTracker(plain).create_issue("o/r", "Fix it", "Body.", ["mh"])
    with_parent = await GitHubTracker(under).create_issue("o/r", "Fix it", "Body.", ["mh"], parent="github:o/r#1")

    assert under.calls == plain.calls, "the parent changes nothing GitHub is asked"
    assert with_parent == made and with_parent.parent is None

    # The router passes the parent on, and the GitHub adapter still ignores it.
    routed = Trackers([GitHubTracker(Apps({("POST", "/repos/o/r/issues"): ISSUE}))], default=lambda: "github")
    assert await routed.create_issue("o/r", "Fix it", "Body.", ["mh"], parent="github:o/r#1") == made


def test_the_label_is_the_deployments_or_the_usual_one_and_a_label_no_tracker_could_hold_is_refused():
    assert registry.check({"default": "github", "trackers": {"github": {}}}, None) == []
    assert registry.check({"default": "github", "trackers": {"github": {}}, "label": "team-bot"}, None) == []
    [reason] = registry.check({"default": "github", "trackers": {"github": {}}, "label": "meta harness"}, None)
    assert reason.startswith("label is the label put on every issue Octofusion creates or changes")


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

    def bound(self, source):
        return self


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
    """Supported trackers are code, one adapter module each; which are set up and the default are configuration.
    What a goal tells a tracker (Jira: its project) is the goal's, not the configuration's."""
    assert list(registry.SUPPORTED) == ["github", "jira"]
    jira = registry.SUPPORTED["jira"]
    assert [s.name for s in jira.settings] == ["site", "projects", "default_project"]
    assert [s.name for s in jira.settings[1].settings] == ["issue_type", "statuses"] and jira.settings[
        2
    ].of == "projects"
    assert [(s.name, s.kind, s.required) for s in jira.goal_settings] == [
        ("project", "choice", True),
        ("assigned", "switch", False),
        ("assignee", "choice", False),
        ("parent", "choice", False),
    ]
    assert jira.takes_assigned({"assigned": "yes"}) and not jira.takes_assigned({})
    assert not registry.SUPPORTED["github"].takes_assigned({"assigned": "yes"})
    assert jira.credentials == ["MH_JIRA_EMAIL", "MH_JIRA_TOKEN"] and jira.writes_progress
    assert [s.name for s in registry.SUPPORTED["github"].goal_settings] == ["assignee"]
    assert not registry.SUPPORTED["github"].writes_progress
    assert registry.check(registry.seed(), None) == [] and registry.seed()["default"] == "github"

    site = {"site": "https://example.atlassian.net"}
    listed = {**site, "default_project": "VL", "projects": {"VL": {"issue_type": "Story"}, "OPS": {}}}
    good = {"default": "jira", "trackers": {"github": {}, "jira": listed}}
    assert registry.check(good, None) == []
    # The default project is required, and is one of the projects listed.
    for jira_settings in (site, {**listed, "default_project": ""}, {**listed, "default_project": "AB"}):
        [reason] = registry.check({"default": "jira", "trackers": {"jira": jira_settings}}, None)
        assert reason.startswith("jira: default_project is required: the project a new goal starts with")
    assert (
        "none yet: add one under Projects first"
        in registry.check({"default": "jira", "trackers": {"jira": site}}, None)[0]
    )
    wrong = {**listed, "projects": {"VL": {"statuses": {"shipped": "Done"}}, "vl": {}}}
    reasons = registry.check({"default": "jira", "trackers": {"jira": wrong}}, None)
    assert any("projects lists the Jira projects Octofusion works in, each by its key" in r for r in reasons)
    just = {**listed, "projects": {"VL": {"statuses": {"shipped": "Done"}, "issue_type": " "}}}
    reasons = registry.check({"default": "jira", "trackers": {"jira": just}}, None)
    assert any(r.startswith("jira: project VL: statuses names the project's status") for r in reasons)
    assert any(r.startswith("jira: project VL: issue_type is the name") for r in reasons)
    assert jira.goal_defaults(listed) == {"project": "VL"} and jira.goal_defaults(site) == {} == jira.goal_defaults(
        None
    )
    assert registry.check({"default": "jira", "trackers": {"github": {}}}, None) == [
        "default must be one of the trackers set up (github), or mh for goals that use no tracker, not 'jira'"
    ]
    # No tracker need be set up: every goal then keeps its tasks in the meta-harness.
    assert registry.check({"trackers": {}}, None) == [] == registry.check({"default": "mh", "trackers": {}}, None)
    assert registry.check({"default": "mh", "trackers": {"github": {}}}, None) == []
    [unset] = registry.check({"default": "jira", "trackers": {}}, None)
    assert unset == "default names 'jira', which is not set up: set it up, or leave default out"
    assert registry.check({"default": "github"}, None)[0].startswith("trackers must be an object")
    assert registry.spec("mh") is registry.OWN and registry.spec("jira") is jira and registry.spec("linear") is None
    bad = {
        "default": "jira",
        "trackers": {"linear": {}, "jira": {"site": "http://example.atlassian.net", "statuses": {"shipped": "Done"}}},
    }
    reasons = registry.check(bad, None)
    assert reasons[0].startswith("linear: not a supported tracker; supported: github, jira")
    assert any("site is the Jira site's address" in r for r in reasons)
    assert any(
        "statuses names the project's status for any of todo, in_progress, in_review, done" in r for r in reasons
    )

    assert jira.check_goal({"project": "VL"}) == [] and registry.SUPPORTED["github"].check_goal({}) == []
    assert "names its project by its key" in jira.check_goal({})[0]
    assert "names its project by its key" in jira.check_goal({"project": "voltron"})[0]
    # A goal names one of the listed projects; a configuration from before projects were listed allows any.
    assert jira.check_goal({"project": "VL"}, listed) == [] == jira.check_goal({"project": "AB"}, site)
    [unlisted] = jira.check_goal({"project": "AB"}, listed)
    assert unlisted.startswith("AB is not one of the Jira projects Octofusion works in (OPS, VL)")
    assert "add it under Configuration, Issue trackers, Jira, Projects" in unlisted


def test_a_goals_tracker_is_bound_to_its_repos_and_what_it_told_the_tracker():
    bound = []

    class Asks(Other):
        def bound(self, source):
            bound.append(source)
            return self

    trackers = Trackers([GitHubTracker(Apps()), Asks()])
    goal = {"id": "g-1", "tracker": "other", "repos": ["o/api", "o/web"], "tracker_settings": {"project": "VL"}}

    trackers.for_goal(goal)

    assert (bound[0].repos, bound[0].settings) == (("o/api", "o/web"), {"project": "VL"})
    github = trackers.adapter("github")
    assert trackers.for_goal({"id": "g-0", "tracker": None, "repos": ["o/r"]}) is github, "GitHub needs no binding"


async def test_a_github_issue_is_closed_as_not_planned_once():
    apps = Apps({("GET", "/repos/o/r/issues/7"): ISSUE})
    tracker = GitHubTracker(apps)

    assert await tracker.close_unplanned(REF) is True
    assert apps.calls[-1] == (
        {"issues": "write"}, "PATCH", "/repos/o/r/issues/7", {"state": "closed", "state_reason": "not_planned"}
    )  # fmt: skip

    apps.answers[("GET", "/repos/o/r/issues/7")] = {**ISSUE, "state": "closed"}
    writes = len([c for c in apps.calls if c[1] == "PATCH"])
    assert await tracker.close_unplanned(REF) is False
    assert len([c for c in apps.calls if c[1] == "PATCH"]) == writes, "one closed already is left alone"
