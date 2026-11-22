# Created by the meta-harness in run t-f8173ad5fb54:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""A Jira goal's `parent` setting: the epics it is picked from, and what a goal may name, against a fake Jira."""

import json

import httpx

from meta_harness.trackers import registry
from meta_harness.trackers.jira import JiraTracker, Site, check_goal

SITE = "https://example.atlassian.net"
TYPES = [
    {"name": "Task", "subtask": False, "hierarchyLevel": 0},
    {"name": "Sub-task", "subtask": True, "hierarchyLevel": -1},
    {"name": "Epic", "subtask": False, "hierarchyLevel": 1},
    {"name": "Initiative", "subtask": False, "hierarchyLevel": 2},
]


class Jira:
    """Jira's project and issue search, as the adapter calls them. Each search is kept, with its JQL."""

    def __init__(self, types: list[dict], pages: list[list[dict]]) -> None:
        self.types, self.pages = types, pages
        self.searches: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/rest/api/2")
        if request.method == "GET" and path == "/project/VL":
            return httpx.Response(200, json={"key": "VL", "issueTypes": self.types})
        if request.method == "POST" and path == "/search/jql":
            ask = json.loads(request.content)
            self.searches.append(ask)
            n = int(ask.get("nextPageToken") or 0)
            last = n + 1 >= len(self.pages)
            page = {"issues": self.pages[n], "isLast": last, **({} if last else {"nextPageToken": str(n + 1)})}
            return httpx.Response(200, json=page)
        return httpx.Response(404, json={"errorMessages": ["Not Found"]})


def adapter(jira: Jira) -> JiraTracker:
    return JiraTracker("bot@example.com", "t", site=lambda: Site(site=SITE), transport=httpx.MockTransport(jira))


def epic(key: str, summary: str) -> dict:
    return {"key": key, "fields": {"summary": summary}}


async def test_the_parent_choices_are_the_projects_open_epics_by_ref():
    jira = Jira(TYPES, [[epic("VL-100", "Checkout"), epic("VL-90", "")], [epic("VL-80", "Search")]])

    found = await adapter(jira).choices("parent", {"project": "VL"})

    assert found == [
        {"value": "jira:VL-100", "label": "VL-100: Checkout"},
        {"value": "jira:VL-90", "label": "VL-90"},
        {"value": "jira:VL-80", "label": "VL-80: Search"},
    ]
    # Asked of the types that hold others, never the sub-task's or a plain task's, and only the open ones.
    jql = jira.searches[0]["jql"]
    assert jql.startswith('project = "VL" AND issuetype in ("Epic", "Initiative") AND statusCategory != Done')
    assert [s.get("nextPageToken") for s in jira.searches] == [None, "1"]


async def test_a_site_that_gives_no_levels_offers_its_epics():
    types = [{"name": "Story", "subtask": False}, {"name": "Epic", "subtask": False}]
    jira = Jira(types, [[epic("VL-7", "Billing")]])

    found = await adapter(jira).choices("parent", {"project": "vl"})

    assert found == [{"value": "jira:VL-7", "label": "VL-7: Billing"}]
    assert 'issuetype in ("Epic")' in jira.searches[0]["jql"]


async def test_no_project_or_no_type_that_holds_others_offers_none():
    jira = Jira([{"name": "Task", "subtask": False, "hierarchyLevel": 0}], [[epic("VL-1", "never asked")]])

    assert await adapter(jira).choices("parent", {"project": "VL"}) == []
    assert await adapter(jira).choices("parent") == [] == await adapter(jira).choices("parent", {"project": "x y"})
    assert jira.searches == []


def test_a_goal_names_a_parent_in_its_own_project_or_none():
    site = {"projects": {"VL": {}, "OPS": {}}}
    assert check_goal({"project": "VL"}, site) == []
    assert check_goal({"project": "VL", "parent": "jira:VL-100"}, site) == []
    for wrong in ("VL-100", "jira:OPS-3", "github:o/r#1"):
        [why] = check_goal({"project": "VL", "parent": wrong}, site)
        assert why.startswith("parent is an epic of the goal's project") and repr(wrong) in why


def test_the_parent_setting_is_a_choice_the_goal_need_not_make():
    [parent] = [s for s in registry.SUPPORTED["jira"].goal_settings if s.name == "parent"]
    assert (parent.kind, parent.required) == ("choice", False)
