# Created by the meta-harness in run t-dbf9b5290b93:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""A split's parts in a tracker with no parents: GitHub and the own tracker create them as they always did.

A Jira split's parts go under the original's parent (tests/planner/test_planning.py and tests/trackers/
test_jira.py prove that, and a refused parent). Here the original has no parent to pass on, so no adapter is
asked for one, and an adapter given one anyway creates the same issue.
"""

import contextlib
from types import SimpleNamespace

import psycopg
import pytest

from meta_harness import db, migrations
from meta_harness.planner import split_issue
from meta_harness.trackers import GitHubTracker, OwnTracker

from .test_planner import Events

REPO = "o/r"
GOAL = {"id": "g-split-no-parent", "label": "meta-harness:goal-split"}
BODY = "Build {}.\n\nPaths: console\n\n## Acceptance checks\n\n- make test\n"
PARTS = [(title, BODY.format(title)) for title in ("One", "Two")]


class Apps:
    """GitHub as the adapter calls it: the original issue 7, and each issue created after it numbered on."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.next = 7

    async def call(self, repo, permissions, method, path, **kwargs):
        self.calls.append((method, path, kwargs.get("json")))
        if method == "POST" and path == f"/repos/{repo}/issues":
            self.next += 1
            return {"number": self.next, "title": kwargs["json"]["title"], "body": "", "state": "open", "labels": []}
        if method == "GET" and path == f"/repos/{repo}/issues/7":
            return {"number": 7, "title": "Too big", "body": "", "state": "open", "labels": []}
        return {}


class Recording:
    """An adapter, with the keyword arguments each create_issue call was given."""

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.asked: list[dict] = []

    def __getattr__(self, name):
        return getattr(self.adapter, name)

    async def create_issue(self, repo, title, body, labels, **kwargs):
        self.asked.append(kwargs)
        return await self.adapter.create_issue(repo, title, body, labels, **kwargs)


@pytest.fixture
def split(monkeypatch):
    """split_issue with the split's claim and record kept in memory."""
    recorded: list[list[str]] = []
    # Only split_issue's own connections: the own tracker keeps its issues in Postgres.
    monkeypatch.setattr(split_issue, "db", SimpleNamespace(connect=lambda **kw: contextlib.nullcontext(None)))
    monkeypatch.setattr(split_issue.store, "claim_split", lambda conn, goal_id, ref, failed_task=None: True)
    monkeypatch.setattr(split_issue.store, "record_split", lambda conn, ref, created, reason: recorded.append(created))

    async def run(original: str, tracker) -> tuple[list[str], Events]:
        events = Events()
        link = {"ref": original, "repo": REPO, "split_into": None}
        made = await split_issue.split_issue(GOAL, link, PARTS, tracker, events, "dave")
        assert recorded == [made]
        return made, events

    return run


async def test_a_github_split_creates_its_parts_with_no_parent_as_before(split):
    apps = Apps()
    tracker = Recording(GitHubTracker(apps))

    made, events = await split("github:o/r#7", tracker)

    assert made == ["github:o/r#8", "github:o/r#9"]
    assert tracker.asked == [{}, {}], "no parent is passed on when the original has none"
    created = [json for method, path, json in apps.calls if (method, path) == ("POST", "/repos/o/r/issues")]
    assert [sorted(fields) for fields in created] == [["body", "labels", "title"]] * 2
    assert [fields["title"] for fields in created] == ["One", "Two"]
    assert "issue_parent_refused" not in events.types()


@pytest.fixture
def own_db():
    # Only a database that cannot be reached skips this: any other failure is the test's to show.
    try:
        with db.connect() as conn:
            migrations.apply(conn)
    except psycopg.OperationalError as err:
        pytest.skip(f"no Postgres at {db.database_url()}: {err}")


@pytest.mark.db
async def test_an_own_tracker_split_creates_its_parts_with_no_parent_as_before(own_db, split, unchecked_text):
    own = OwnTracker()
    original = await own.create_issue(REPO, "Too big", BODY.format("it all"), [])
    tracker = Recording(own)

    made, events = await split(original.ref, tracker)

    assert tracker.asked == [{}, {}], "no parent is passed on when the original has none"
    parts = [await own.read(ref) for ref in made]
    assert [(p.title, p.repo, p.parent) for p in parts] == [("One", REPO, None), ("Two", REPO, None)]
    assert "issue_parent_refused" not in events.types()


@pytest.mark.db
async def test_the_own_tracker_ignores_a_parent_it_is_given(own_db, unchecked_text):
    own = OwnTracker()

    plain = await own.create_issue(REPO, "One", BODY.format("one"), ["x"])
    under = await own.create_issue(REPO, "One", BODY.format("one"), ["x"], parent="mh:i-000000000000")

    assert under.parent is None is plain.parent
    keep = ("repo", "title", "body", "labels", "state", "checks", "paths", "parent")
    assert [getattr(under, k) for k in keep] == [getattr(plain, k) for k in keep]
