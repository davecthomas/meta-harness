"""The Jira adapter against Jira's REST API as it answers, with no call leaving the test."""

import json

import httpx
import pytest

from meta_harness.trackers import DONE, IN_PROGRESS, IN_REVIEW, TODO, Source, TrackerError
from meta_harness.trackers.jira import JiraTracker, Site, check_goal, markdown, repo_for

SITE = "https://example.atlassian.net"
# A goal with one repo, whose issues are in the project VL.
GOAL = Source(repos=("o/r",), settings={"project": "VL"})


def status(name: str, category: str) -> dict:
    return {"name": name, "statusCategory": {"key": category}}


def issue(key: str, summary: str = "Fix it", description: str = "", state=("To Do", "new"), **fields) -> dict:
    base = {"summary": summary, "description": description, "labels": ["mh"], "status": status(*state)}
    return {"key": key, "fields": {**base, "updated": "2026-11-17T10:00:00.000+0000", **fields}}


class Jira:
    """What the adapter asked, and what each method and path answers."""

    def __init__(self, answers: dict | None = None) -> None:
        self.asked: list[tuple[str, str, dict | None]] = []
        self.answers = answers or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.asked.append((request.method, request.url.path, body))
        assert request.headers["authorization"].startswith("Basic "), "every call signs in"
        answer = self.answers.get((request.method, request.url.path), {})
        if callable(answer):
            answer = answer(body)
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)


def tracker(jira: Jira, source: Source | None = GOAL, statuses: dict | None = None, **sign_in) -> JiraTracker:
    """The adapter for a goal (source), or with no goal (None), against the fake Jira."""
    sign_in = {"email": "bot@example.com", "token": "t", **sign_in}
    site = Site(site=SITE, statuses=statuses or {})
    adapter = JiraTracker(site=lambda: site, transport=httpx.MockTransport(jira), **sign_in)
    return adapter.bound(source) if source else adapter


DESCRIPTION = "Make it so.\n\nh2. Acceptance checks\n* {{uv run pytest -q}}\n\nPaths: console\nDepends on: VL-3, OPS-9"


async def test_open_issues_are_read_by_project_and_label_a_page_at_a_time():
    pages = {
        None: {"issues": [issue("VL-7", description=DESCRIPTION)], "nextPageToken": "p2"},
        "p2": {"issues": [issue("VL-8")]},
    }
    jira = Jira({("POST", "/rest/api/2/search/jql"): lambda body: pages[body.get("nextPageToken")]})

    found = await tracker(jira).list_open("o/r", 'goal "a"')

    assert [i.ref for i in found] == ["jira:VL-7", "jira:VL-8"]
    first = found[0]
    assert (first.repo, first.number, first.state, first.labels) == ("o/r", 7, "open", ["mh"])
    assert first.checks == ["uv run pytest -q"] and first.paths == ["console"]
    assert first.depends_on == ["jira:VL-3", "jira:OPS-9"]
    assert (
        jira.asked[0][2]["jql"]
        == 'project = "VL" AND statusCategory != Done AND labels = "goal \\"a\\"" ORDER BY created ASC'
    )
    assert jira.asked[1][2]["nextPageToken"] == "p2"


async def test_an_issue_is_read_with_what_blocks_it_and_done_is_closed():
    blocked = [
        {"type": {"inward": "is blocked by"}, "inwardIssue": {"key": "VL-2"}},
        {"type": {"inward": "is cloned by"}, "inwardIssue": {"key": "VL-4"}},
        {"type": {"outward": "blocks"}, "outwardIssue": {"key": "VL-5"}},
    ]
    jira = Jira({("GET", "/rest/api/2/issue/VL-7"): issue("VL-7", state=("Shipped", "done"), issuelinks=blocked)})
    adapter = tracker(jira)

    read = await adapter.read("jira:VL-7")

    assert (read.state, read.depends_on, read.repo) == ("closed", ["jira:VL-2"], "o/r")
    assert adapter.url("jira:VL-7") == f"{SITE}/browse/VL-7" and adapter.url("github:o/r#1") is None
    assert adapter.repo_of("jira:VL-1") == "o/r" and adapter.issue_url_prefix() == f"{SITE}/browse/"
    with pytest.raises(ValueError, match="is not jira:<PROJECT>-<number>"):
        adapter.repo_of("jira:vl-7")
    with pytest.raises(ValueError, match="OPS-1 is not in VL, the goal's Jira project"):
        adapter.repo_of("jira:OPS-1")

    # With no goal, an issue is still read by its ref, for its state; which repo it is for is its goal's to say.
    alone = tracker(jira, source=None)
    assert (await alone.read("jira:VL-7")).repo == ""
    with pytest.raises(ValueError, match="a Jira issue's repo is its goal's"):
        alone.repo_of("jira:VL-7")
    with pytest.raises(TrackerError, match="no Jira project is named: a goal that uses Jira names its project"):
        await alone.list_open("o/r", None)


async def test_a_goal_with_several_repos_reads_each_issues_repo_from_the_issue_and_holds_one_that_names_none():
    """One Jira project can hold work for several repos: the issue says which, by a Repo: line or a repo: label."""
    several = Source(repos=("o/api", "o/web"), settings={"project": "VL"})
    found = [
        issue("VL-1", description="Fix it.\n\nRepo: o/web"),
        issue("VL-2", labels=["mh", "repo:api"]),
        issue("VL-3", description="Which repo?"),
        issue("VL-4", description="Repo: o/elsewhere"),
    ]
    jira = Jira({("POST", "/rest/api/2/search/jql"): {"issues": found}})
    adapter = tracker(jira, source=several)

    api, web = await adapter.list_open("o/api", "mh"), await adapter.list_open("o/web", "mh")

    assert [(i.ref, i.repo) for i in api] == [("jira:VL-2", "o/api"), ("jira:VL-3", ""), ("jira:VL-4", "")]
    assert [(i.ref, i.repo) for i in web] == [("jira:VL-1", "o/web")]
    assert len(jira.asked) == 1, "one search of the project serves every repo of the goal"
    assert repo_for(("o/r",), "", []) == "o/r", "a goal with one repo needs nothing from the issue"
    assert repo_for(("o/api", "x/api"), "Repo: api", []) == "", "a name two repos share settles nothing"
    assert repo_for(("o/api", "x/api"), "Repo: x/api", []) == "x/api"


async def test_an_issue_is_created_in_the_goals_project_and_a_label_with_a_space_is_refused():
    jira = Jira(
        {("POST", "/rest/api/2/issue"): {"key": "VL-9"}, ("GET", "/rest/api/2/issue/VL-9"): issue("VL-9", "Split 1")}
    )
    adapter = tracker(jira)

    made = await adapter.create_issue("o/r", "Split 1", "Body.", ["mh"])

    assert made.ref == "jira:VL-9"
    assert jira.asked[0][2] == {
        "fields": {
            "project": {"key": "VL"},
            "summary": "Split 1",
            "description": "Body.",
            "issuetype": {"name": "Task"},
            "labels": ["mh"],
        }
    }
    await adapter.ensure_label("o/r", "meta-harness:goal-1")
    with pytest.raises(TrackerError, match="a Jira label has no spaces"):
        await adapter.ensure_label("o/r", "my goal")

    # A goal with several repos: the new issue says which one it is for, as it is read back.
    several = tracker(jira, source=Source(repos=("o/api", "o/web"), settings={"project": "VL"}))
    await several.create_issue("o/web", "Split 2", "Body.", ["mh"])
    posted = [body for method, path, body in jira.asked if (method, path) == ("POST", "/rest/api/2/issue")]
    assert posted[-1]["fields"]["description"] == "Body.\n\nRepo: o/web\n"


async def test_the_sites_projects_are_listed_for_a_goal_to_choose_from():
    pages = {
        "0": {"values": [{"key": "VL", "name": "Voltron"}, {"key": "OPS", "name": "Operations"}], "isLast": False},
        "2": {"values": [{"key": "AB", "name": "Abacus"}], "isLast": True},
    }

    def projects(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[request.url.params["startAt"]])

    adapter = JiraTracker("bot@example.com", "t", site=lambda: Site(site=SITE), transport=httpx.MockTransport(projects))

    assert await adapter.choices("project") == [
        {"value": "AB", "label": "Abacus (AB)"},
        {"value": "OPS", "label": "Operations (OPS)"},
        {"value": "VL", "label": "Voltron (VL)"},
    ]
    assert await adapter.choices("anything else") == []


async def test_a_comment_and_a_pull_requests_link_are_added_to_the_issue():
    jira = Jira({("POST", "/rest/api/2/issue/VL-7/comment"): {"id": "10042"}})
    adapter = tracker(jira)

    assert await adapter.comment("jira:VL-7", "The task started.") == f"{SITE}/browse/VL-7?focusedCommentId=10042"
    await adapter.link_pull_request("jira:VL-7", "https://github.com/o/r/pull/9", "Fix it")

    assert jira.asked[0][2] == {"body": "The task started."}
    assert jira.asked[1] == (
        "POST",
        "/rest/api/2/issue/VL-7/remotelink",
        {
            "globalId": "https://github.com/o/r/pull/9",
            "object": {"url": "https://github.com/o/r/pull/9", "title": "Fix it"},
        },
    )


MOVES = {
    "transitions": [
        {"id": "11", "to": status("To Do", "new")},
        {"id": "21", "to": status("In Progress", "indeterminate")},
        {"id": "31", "to": status("In Review", "indeterminate")},
        {"id": "41", "to": status("Done", "done")},
    ]
}


def workflow(now: tuple[str, str], moves: dict = MOVES) -> Jira:
    return Jira(
        {
            ("GET", "/rest/api/2/issue/VL-7"): issue("VL-7", state=now),
            ("GET", "/rest/api/2/issue/VL-7/transitions"): moves,
        }
    )


def moved(jira: Jira) -> list[str]:
    return [
        body["transition"]["id"]
        for method, path, body in jira.asked
        if method == "POST" and path.endswith("/transitions")
    ]


@pytest.mark.parametrize(
    ("now", "wanted", "move"),
    [
        (("To Do", "new"), IN_PROGRESS, ["21"]),
        (("In Progress", "indeterminate"), IN_PROGRESS, []),
        (("In Progress", "indeterminate"), IN_REVIEW, ["31"]),
        (("In Review", "indeterminate"), IN_REVIEW, []),
        (("In Review", "indeterminate"), IN_PROGRESS, ["21"]),
        (("In Review", "indeterminate"), DONE, ["41"]),
        (("Done", "done"), DONE, []),
        (("Done", "done"), TODO, ["11"]),
    ],
)
async def test_a_status_moves_the_issue_through_its_workflow_once(now, wanted, move):
    jira = workflow(now)

    assert await tracker(jira).set_status("jira:VL-7", wanted) is bool(move)
    assert moved(jira) == move


async def test_a_workflow_with_no_review_status_leaves_an_issue_in_progress_and_a_named_status_is_used():
    plain = {"transitions": [m for m in MOVES["transitions"] if m["id"] != "31"]}
    jira = workflow(("In Progress", "indeterminate"), plain)
    assert await tracker(jira).set_status("jira:VL-7", IN_REVIEW) is False and moved(jira) == []

    odd = {"transitions": [{"id": "41", "to": status("Done", "done")}, {"id": "51", "to": status("Shipped", "done")}]}
    jira = workflow(("In Review", "indeterminate"), odd)
    assert await tracker(jira, statuses={"done": "Shipped"}).set_status("jira:VL-7", DONE) is True and moved(jira) == [
        "51"
    ]


async def test_a_status_is_found_by_its_usual_name_where_the_workflows_categories_mislead():
    """A real workflow: Ready for Development is classed as in progress, In Code Review as not started, and two
    statuses are classed done. The usual names decide before the categories do."""
    odd = {
        "transitions": [
            {"id": "1", "to": status("Ready for Development", "indeterminate")},
            {"id": "2", "to": status("In Code Review", "new")},
            {"id": "3", "to": status("In Dev", "indeterminate")},
            {"id": "4", "to": status("Won't Do", "done")},
            {"id": "5", "to": status("Done", "done")},
            {"id": "6", "to": status("In Progress", "indeterminate")},
        ]
    }
    for now, wanted, move in (
        (("Ready for Development", "indeterminate"), IN_PROGRESS, ["6"]),
        (("In Progress", "indeterminate"), IN_REVIEW, ["2"]),
        (("In Code Review", "new"), IN_REVIEW, []),
        (("In Code Review", "new"), IN_PROGRESS, ["6"]),
        (("In Code Review", "new"), DONE, ["5"]),
        (("In Dev", "indeterminate"), IN_PROGRESS, []),
    ):
        jira = workflow(now, odd)
        assert await tracker(jira).set_status("jira:VL-7", wanted) is bool(move), (now, wanted)
        assert moved(jira) == move, (now, wanted)


async def test_a_workflow_with_no_way_there_says_where_the_issue_can_go_and_what_to_do():
    stuck = {"transitions": [{"id": "11", "to": status("To Do", "new")}]}

    with pytest.raises(TrackerError, match="VL-7 is 'Blocked' and its workflow has no move to 'done'") as failed:
        await tracker(workflow(("Blocked", "indeterminate"), stuck)).set_status("jira:VL-7", DONE)
    assert "it can go to: To Do" in str(failed.value) and "Configuration, Issue trackers, Jira" in str(failed.value)
    assert not failed.value.retryable


async def test_a_refusal_says_why_and_what_to_check_and_a_missing_sign_in_says_which_secrets():
    expired = httpx.Response(401, json={"errorMessages": ["Client must be authenticated"]})
    with pytest.raises(TrackerError, match="Client must be authenticated") as refused:
        await tracker(Jira({("GET", "/rest/api/2/issue/VL-7"): expired})).read("jira:VL-7")
    assert "the token may have expired" in str(refused.value) and refused.value.status == 401
    assert not refused.value.retryable

    busy = httpx.Response(503, text="try later")
    with pytest.raises(TrackerError) as later:
        await tracker(Jira({("GET", "/rest/api/2/issue/VL-7"): busy})).read("jira:VL-7")
    assert later.value.retryable

    signed_out = tracker(Jira(), token="")
    assert not signed_out.connected
    with pytest.raises(TrackerError, match="Jira is not signed in: add the secrets MH_JIRA_EMAIL"):
        await signed_out.read("jira:VL-7")


def test_a_jira_description_reads_as_the_markdown_the_planner_parses():
    assert markdown("h2. Acceptance checks\n* {{make test}}\n{code:bash}\nls\n{code}") == (
        "## Acceptance checks\n* `make test`\n```\nls\n```"
    )


async def test_a_goal_that_asks_takes_the_issues_assigned_to_the_meta_harness_and_any_other_reads_none():
    jira = Jira({("POST", "/rest/api/2/search/jql"): {"issues": [issue("VL-7", labels=[])]}})
    asks = Source(repos=("o/r",), settings={"project": "VL", "assigned": "yes"})

    found = await tracker(jira, asks).list_assigned("o/r")

    assert [i.ref for i in found] == ["jira:VL-7"]
    assert (
        jira.asked[0][2]["jql"]
        == 'project = "VL" AND statusCategory != Done AND assignee = currentUser() ORDER BY created ASC'
    )

    quiet = Jira()
    assert await tracker(quiet).list_assigned("o/r") == [] and quiet.asked == []


def test_a_goals_assigned_setting_is_yes_or_left_out():
    assert check_goal({"project": "VL", "assigned": "yes"}) == [] and check_goal({"project": "VL"}) == []
    [reason] = check_goal({"project": "VL", "assigned": "true"})
    assert 'assigned is "yes"' in reason
