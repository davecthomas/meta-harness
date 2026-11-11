"""A pass of the supervisor against Postgres, with the Task API, Temporal and the code host faked."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from meta_harness import db, migrations
from meta_harness.planner import store as goal_store
from meta_harness.run_service.code_host import PullRequest
from meta_harness.supervisor import standing
from meta_harness.supervisor.loop import GRACE, Supervisor, spent_so_far
from meta_harness.tasks.client import TaskApiError

pytestmark = pytest.mark.db
REPO = "davecthomas/meta-harness"


@pytest.fixture(scope="module", autouse=True)
def migrated():
    try:
        with db.connect() as conn:
            migrations.apply(conn)
    except Exception as err:  # noqa: BLE001
        pytest.skip(f"no Postgres at {db.database_url()}: {err}")


@pytest.fixture(autouse=True)
def clean():
    with db.connect(autocommit=True) as conn:
        conn.execute("TRUNCATE supervisor.standing, scheduler.repo_area_lock")
    yield


class Tasks:
    """The Task API as the supervisor uses it."""

    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self.recovered: list[tuple[str, str, str]] = []
        self.canceled: list[tuple[str, str]] = []
        self.refuse: str | None = None

    def add(self, status: str, **fields) -> dict:
        task_id = f"t-{uuid.uuid4().hex[:8]}"
        self.tasks[task_id] = {"id": task_id, "status": status, "repo": REPO, "goal_id": None, **fields}
        return self.tasks[task_id]

    def page(self, statuses=None, goal_id=None, limit=25, offset=0):
        found = [t for t in self.tasks.values() if not statuses or t["status"] in statuses]
        return {"tasks": found[offset : offset + limit], "total": len(found)}

    def recover(self, task_id, why, spent_usd="0"):
        if self.refuse:
            raise TaskApiError(503, self.refuse)
        self.recovered.append((task_id, why, spent_usd))
        task = self.tasks[task_id]
        task["status"] = "canceled" if task.get("cancel_requested") else "queued"
        return task

    def cancel(self, task_id, reason="", actor=None):
        self.canceled.append((task_id, reason))
        self.tasks[task_id]["status"] = "canceled"
        return self.tasks[task_id]

    def set_status(self, task_id, status, reason=None, **fields):
        self.tasks[task_id]["status"] = status
        return self.tasks[task_id]


class Events:
    def __init__(self):
        self.emitted: list[tuple[str, dict, dict]] = []
        self.stored: list[dict] = []

    def emit(self, event_type, payload=None, **fields):
        self.emitted.append((event_type, payload or {}, fields))
        return "id"

    def read(self, from_position=0, limit=500, **filters):
        wanted = filters.get("event_type", "").split(",")
        found = [e for e in self.stored if e["task_id"] == filters.get("task_id") and e["event_type"] in wanted]
        return {"events": found}


class Workflows:
    def __init__(self):
        self.states: dict[str, str] = {}

    async def state(self, task_id):
        return self.states.get(task_id, "none")


class Host:
    def __init__(self):
        self.pulls: dict[int, str] = {}
        self.reads = 0

    async def read_pull_request(self, repo, number):
        self.reads += 1
        state = self.pulls[number]
        return PullRequest(repo, number, f"https://github.com/{repo}/pull/{number}", state, "abc", "def")


@pytest.fixture
def world():
    tasks, events, workflows, host = Tasks(), Events(), Workflows(), Host()
    return Supervisor(tasks, events, workflows, host), tasks, events, workflows, host


def _goal(status: str) -> str:
    with db.connect(autocommit=True) as conn:
        new = goal_store.NewGoal(statement="Fix bugs", repos=[REPO], label=f"mh:test-{uuid.uuid4().hex[:6]}")
        goal = goal_store.create(conn, "dave", new)
        if status != goal["status"]:
            conn.execute("UPDATE planner.goal SET status = %s WHERE id = %s", (status, goal["id"]))
        return goal["id"]


def _standing(task_id: str) -> dict | None:
    with db.connect(autocommit=True) as conn:
        return standing.get(conn, task_id)


def _admit(task_id: str) -> None:
    with db.connect(autocommit=True) as conn:
        conn.execute(
            "INSERT INTO scheduler.repo_area_lock (task_id, repo, area, expires_at) VALUES (%s, %s, '', now() + interval '1 hour')",
            (task_id, REPO),
        )


async def test_a_running_task_with_no_workflow_is_recovered_once_it_has_stood_so_for_the_grace(world):
    supervisor, tasks, events, workflows, _ = world
    orphan = tasks.add("running", pr_url=None, waiting_on="budget")
    working = tasks.add("running")
    workflows.states = {orphan["id"]: "ended", working["id"]: "running"}
    events.stored = [
        {"task_id": orphan["id"], "event_type": "attempt_finished", "payload": {"spent_usd": "1.25"}},
        {"task_id": orphan["id"], "event_type": "task_retried", "payload": {}},
        {"task_id": orphan["id"], "event_type": "attempt_finished", "payload": {"spent_usd": "2.50"}},
        {"task_id": orphan["id"], "event_type": "attempt_finished", "payload": {"spent_usd": "0.75"}},
    ]
    start = datetime.now(UTC)

    first = await supervisor.pass_once(start)

    assert first.recovered == [] and tasks.recovered == [], "a status write may still be on its way"
    found = _standing(orphan["id"])
    assert (found["owner"], found["situation"]) == ("supervisor", "no_workflow")
    assert _standing(working["id"])["owner"] == "workflow"
    assert first.owners == {"supervisor": 1, "workflow": 1}
    assert [(p["owner"], f["task_id"]) for t, p, f in events.emitted if t == "task_standing"] == [
        ("supervisor", orphan["id"]),
        ("workflow", working["id"]),
    ]

    later = await supervisor.pass_once(start + GRACE + timedelta(seconds=30))

    assert later.recovered == [orphan["id"]]
    assert tasks.recovered == [(orphan["id"], "its RunTask workflow ended without ending the task", "3.25")]
    assert spent_so_far(events, orphan["id"]) == Decimal("3.25"), "what it spent since its last retry is carried"


async def test_a_task_asked_to_cancel_with_no_workflow_is_canceled_not_started_again(world):
    supervisor, tasks, _, workflows, _ = world
    task = tasks.add("running", cancel_requested=True)
    start = datetime.now(UTC)
    await supervisor.pass_once(start)

    result = await supervisor.pass_once(start + GRACE + timedelta(seconds=30))

    assert result.ended == [task["id"]] and task["status"] == "canceled"


async def test_a_queued_task_the_scheduler_started_whose_workflow_is_gone_is_recovered(world):
    supervisor, tasks, _, workflows, _ = world
    lost, waiting = tasks.add("queued"), tasks.add("queued")
    _admit(lost["id"])
    workflows.states = {lost["id"]: "ended"}
    start = datetime.now(UTC)
    await supervisor.pass_once(start)

    result = await supervisor.pass_once(start + GRACE + timedelta(seconds=30))

    assert result.recovered == [lost["id"]]
    assert _standing(waiting["id"])["owner"] == "scheduler"


async def test_an_unreachable_temporal_moves_no_task(world):
    supervisor, tasks, _, workflows, _ = world
    task = tasks.add("running")
    workflows.states = {task["id"]: "unknown"}
    start = datetime.now(UTC)
    await supervisor.pass_once(start)

    result = await supervisor.pass_once(start + GRACE * 4)

    assert result.recovered == [] and tasks.recovered == []
    assert _standing(task["id"])["situation"] == "workflow_unknown"


async def test_a_recovery_the_task_service_refuses_is_tried_again_after_a_longer_wait_each_time(world):
    supervisor, tasks, _, workflows, _ = world
    task = tasks.add("running")
    tasks.refuse = "the task service is down"
    start = datetime.now(UTC)
    await supervisor.pass_once(start)

    failed = await supervisor.pass_once(start + GRACE + timedelta(seconds=30))

    assert failed.recovered == [] and "the task service is down" in failed.errors[0]
    found = _standing(task["id"])
    assert found["attempts"] == 1 and "the task service is down" in found["last_error"]
    tasks.refuse = None
    assert (await supervisor.pass_once(datetime.now(UTC) + timedelta(seconds=20))).recovered == [], "too soon"
    assert (await supervisor.pass_once(datetime.now(UTC) + GRACE + timedelta(minutes=2))).recovered == [task["id"]]


async def test_a_parked_task_no_sweep_covers_is_ended_when_its_pull_request_ends(world):
    supervisor, tasks, events, _, host = world
    url = f"https://github.com/{REPO}/pull"
    merged = tasks.add("ready", pr_url=f"{url}/11")
    closed = tasks.add("review_unresolved", pr_url=f"{url}/12", goal_id=_goal("paused"))
    open_ = tasks.add("ready", pr_url=f"{url}/13")
    swept = tasks.add("ready", pr_url=f"{url}/14", goal_id=_goal("active"))
    host.pulls = {11: "merged", 12: "closed", 13: "open", 14: "merged"}

    result = await supervisor.pass_once()

    assert sorted(result.ended) == sorted([merged["id"], closed["id"]])
    assert (merged["status"], closed["status"], open_["status"]) == ("closed", "canceled", "ready")
    assert swept["status"] == "ready" and _standing(swept["id"])["owner"] == "planner", "its goal's sweep ends it"
    ask = _standing(open_["id"])
    assert ask["owner"] == "operator" and "Merge the pull request" in ask["ask"]
    assert {t for t, _, _ in events.emitted} >= {"pull_request_merged", "pull_request_closed"}
    # A pull request is read again only after a while, not on every pass.
    await supervisor.pass_once()
    reads = host.reads
    await supervisor.pass_once()
    assert host.reads == reads


async def test_tasks_of_an_ended_goal_that_have_not_started_are_canceled(world):
    supervisor, tasks, _, _, _ = world
    canceled_goal, met_goal, active_goal = _goal("canceled"), _goal("met"), _goal("active")
    queued = tasks.add("queued", goal_id=canceled_goal)
    blocked = tasks.add("blocked", goal_id=met_goal)
    kept = tasks.add("blocked", goal_id=active_goal)

    result = await supervisor.pass_once()

    assert sorted(result.canceled) == sorted([queued["id"], blocked["id"]])
    assert dict(tasks.canceled) == {
        queued["id"]: "its goal was canceled, so it will never start",
        blocked["id"]: "its goal was completed, so it will never start",
    }
    assert kept["status"] == "blocked" and _standing(kept["id"])["owner"] == "planner"


async def test_a_closed_task_is_forgotten_and_a_failed_one_names_what_an_operator_does(world):
    supervisor, tasks, _, _, _ = world
    failed, done = tasks.add("failed"), tasks.add("running")
    await supervisor.pass_once()
    done["status"] = "closed"

    await supervisor.pass_once()

    assert _standing(done["id"]) is None
    found = _standing(failed["id"])
    assert found["owner"] == "operator" and "retry it" in found["ask"]
