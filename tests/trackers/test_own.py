"""The meta-harness's own tracker: issues kept in the meta-harness and nowhere else."""

import pytest

import psycopg

from meta_harness import db, migrations
from meta_harness.trackers import DONE, IN_PROGRESS, Issue, OwnTracker, Source, TrackerError, Trackers
from meta_harness.trackers.own import parse_ref
from meta_harness.trackers.router import WithOwn

pytestmark = pytest.mark.db
REPO = "o/r"
BODY = "Build the store.\n\n## Acceptance checks\n\n- make test\n\nPaths: src/store\n"


@pytest.fixture(scope="module", autouse=True)
def migrated():
    # Only a database that cannot be reached skips these: any other failure is the tests' to show.
    try:
        with db.connect() as conn:
            migrations.apply(conn)
    except psycopg.OperationalError as err:
        pytest.skip(f"no Postgres at {db.database_url()}: {err}")


async def test_an_issue_is_created_read_and_listed_by_its_label_like_any_trackers():
    own = OwnTracker()
    label = f"goal-{id(own)}"

    made = await own.create_issue(REPO, "The store", BODY, [label, label, "backend"])

    assert parse_ref(made.ref) and made.ref.startswith("mh:i-")
    assert (made.repo, made.title, made.state, made.labels) == (REPO, "The store", "open", [label, "backend"])
    assert (made.checks, made.paths) == (["make test"], ["src/store"])
    assert (await own.read(made.ref)).title == "The store"
    assert [i.ref for i in await own.list_open(REPO, label)] == [made.ref]
    assert made.ref in [i.ref for i in await own.list_open("O/R", None)], "a repo's name is matched in any case"
    assert await own.list_open("other/repo", label) == [] and await own.list_assigned(REPO) == []
    assert own.repo_of(made.ref) == REPO and own.url(made.ref) is None


async def test_a_task_waits_on_the_tasks_its_depends_on_line_names():
    own = OwnTracker()
    first = await own.create_issue(REPO, "First", BODY, [])
    second = await own.create_issue(REPO, "Second", f"{BODY}\nDepends on: {first.ref}, mh:i-nothexdigits\n", [])

    assert (await own.read(second.ref)).depends_on == [first.ref]


async def test_done_closes_the_issue_and_its_progress_is_kept_with_it():
    own = OwnTracker()
    made = await own.create_issue(REPO, "The store", BODY, ["x"])

    assert await own.set_status(made.ref, IN_PROGRESS) and not await own.set_status(made.ref, IN_PROGRESS)
    assert (await own.read(made.ref)).state == "open"
    await own.link_pull_request(made.ref, "https://github.com/o/r/pull/9", "The store")
    assert await own.comment(made.ref, "A pull request is open.") == ""
    await own.mark(made.ref)
    assert await own.set_status(made.ref, DONE)

    assert (await own.read(made.ref)).state == "closed"
    assert made.ref not in [i.ref for i in await own.list_open(REPO, "x")]
    with db.connect() as conn:
        row = conn.execute(
            "SELECT pr_url, comments FROM planner.own_issue WHERE id = %s", (parse_ref(made.ref),)
        ).fetchone()
    assert row == ("https://github.com/o/r/pull/9", [{"body": "A pull request is open."}])


async def test_a_task_that_is_not_kept_says_so_and_how_to_go_on():
    own = OwnTracker()

    for call in (
        own.read("mh:i-000000000000"),
        own.set_status("mh:i-000000000000", DONE),
        own.comment("mh:i-000000000000", "x"),
    ):
        with pytest.raises(
            TrackerError, match="is not a task the meta-harness keeps.*Remove it from the goal"
        ) as refused:
            await call
        assert not refused.value.retryable
    with pytest.raises(TrackerError, match="is not mh:i-"):
        await own.read("mh:nope")
    with pytest.raises(ValueError):
        own.repo_of("github:o/r#1")


class Primary:
    """A goal's outside tracker, which shows a task's progress by itself as GitHub does."""

    scheme = "github"
    writes_progress = False

    def __init__(self):
        self.calls = []
        self.source = None

    def bound(self, source):
        self.source = source
        return self

    async def list_open(self, repo, label):
        self.calls.append(("list_open", label))
        return [Issue(ref="github:o/r#1", repo=repo, number=1, title="Theirs", body="", labels=[], state="open")]

    async def list_assigned(self, repo):
        return []

    async def read(self, ref):
        self.calls.append(("read", ref))
        return Issue(ref=ref, repo=REPO, number=1, title="Theirs", body="", labels=[], state="open")

    async def create_issue(self, repo, title, body, labels):
        self.calls.append(("create", title))
        return Issue(ref="github:o/r#2", repo=repo, number=2, title=title, body=body, labels=labels, state="open")

    async def set_status(self, ref, status):
        self.calls.append(("set_status", ref))
        return True

    async def comment(self, ref, body):
        self.calls.append(("comment", ref))
        return "https://x"

    def repo_of(self, ref):
        return REPO

    def url(self, ref):
        return "https://github.com/o/r/issues/1"

    def choices(self, setting):
        return ["only the goal's tracker has this"]


async def test_a_tracked_goal_reads_and_tells_each_issue_through_the_tracker_its_ref_names():
    primary, own = Primary(), OwnTracker()
    trackers = Trackers([primary, own], default=lambda: "github")
    kept = await own.create_issue(REPO, "Kept here", BODY, ["goal-x"])

    goal = trackers.for_goal({"id": "g-1", "tracker": "github", "repos": [REPO]})

    assert isinstance(goal, WithOwn) and goal.scheme == "github" and primary.source == Source((REPO,), {})
    assert (await goal.read(kept.ref)).title == "Kept here" and (await goal.read("github:o/r#1")).title == "Theirs"
    assert [i.title for i in await goal.list_open(REPO, "goal-x")] == ["Theirs", "Kept here"]
    assert [i.title for i in await goal.list_open(REPO, None)] == ["Theirs"], "what there is to pick is the tracker's"
    # Its own tasks are told how they go and closed; an outside tracker that shows progress itself is left alone.
    assert await goal.set_status(kept.ref, DONE) and (await own.read(kept.ref)).state == "closed"
    assert not await goal.set_status("github:o/r#1", DONE) and await goal.comment("github:o/r#1", "x") == ""
    assert ("set_status", "github:o/r#1") not in primary.calls and ("comment", "github:o/r#1") not in primary.calls
    assert (await goal.create_issue(REPO, "New", "", [])).ref == "github:o/r#2", (
        "a new issue goes to the goal's tracker"
    )
    assert goal.repo_of(kept.ref) == REPO and goal.url(kept.ref) is None and goal.url("github:o/r#1")
    assert goal.choices("project") == ["only the goal's tracker has this"]


def test_a_goal_with_no_tracker_uses_the_own_tracker_alone():
    own = OwnTracker()
    trackers = Trackers([Primary(), own], default=lambda: "mh")

    untracked = trackers.for_goal({"id": "g-2", "tracker": "mh", "repos": [REPO]})

    assert isinstance(untracked, OwnTracker) and untracked.source == Source((REPO,), {})
