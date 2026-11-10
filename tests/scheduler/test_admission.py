import uuid
from decimal import Decimal

import pytest

from meta_harness import db, migrations
from meta_harness.scheduler.admission import Admission, areas_for, conflicts

pytestmark = pytest.mark.db


@pytest.fixture(scope="module", autouse=True)
def migrated():
    try:
        with db.connect() as conn:
            migrations.apply(conn)
    except Exception as err:  # noqa: BLE001
        pytest.skip(f"no Postgres at {db.database_url()}: {err}")


class Tasks:
    def __init__(self):
        self.tasks: dict[str, dict] = {}

    def add(self, repo, status="queued", priority=0, paths=None, goal_id=None):
        task_id = f"t-{uuid.uuid4().hex[:10]}"
        self.tasks[task_id] = {
            "id": task_id,
            "repo": repo,
            "status": status,
            "priority": priority,
            "paths": paths or [],
            "goal_id": goal_id,
            "n": len(self.tasks),
        }
        return task_id

    def list(self, statuses, goal_id, limit):
        rows = [t for t in self.tasks.values() if (not statuses or t["status"] in statuses)]
        rows = [t for t in rows if goal_id is None or t["goal_id"] == goal_id]
        return sorted(rows, key=lambda t: (-t["priority"], t["n"]))[:limit]

    def get(self, task_id):
        return self.tasks[task_id]


class Goals:
    def __init__(self):
        self.goals = {}

    def get(self, goal_id):
        return self.goals[goal_id]


class Events:
    def __init__(self, spent=None):
        self.spent = spent or {}
        self.emitted = []

    def emit(self, event_type, payload=None, **fields):
        self.emitted.append((event_type, payload, fields.get("task_id")))

    def read(self, from_position, limit, task_id=None, event_type=None):
        return {"events": [{"payload": {"spent_usd": str(s)}} for s in self.spent.get(task_id, [])]}


@pytest.fixture
def repo():
    # The concurrency limit counts every lock, so each test starts with no locks from earlier tests.
    with db.connect(autocommit=True) as conn:
        conn.execute("DELETE FROM scheduler.repo_area_lock WHERE repo LIKE 'test/%'")
    return f"test/repo-{uuid.uuid4().hex[:8]}"


def _admission(tasks, goals=None, events=None, max_running=5):
    started = []

    async def start(task_id):
        started.append(task_id)
        tasks.tasks[task_id]["status"] = "running"

    return Admission(tasks, goals, events or Events(), start, max_running), started


def test_areas_and_conflicts():
    assert areas_for({"paths": ["src/a.py", "/src/b.py", "docs/x.md"]}) == ["docs", "src"]
    assert areas_for({"paths": []}) == ["*"]
    assert conflicts(["*"], ["docs"]) and conflicts(["src"], ["src", "docs"]) and not conflicts(["src"], ["docs"])


async def test_two_tasks_on_one_repo_area_run_in_turn(repo):
    tasks = Tasks()
    first, second = tasks.add(repo, priority=1), tasks.add(repo)
    admission, started = _admission(tasks)
    result = await admission.pass_once()
    assert result.started == [first]
    assert "held by" in result.held[second]
    tasks.tasks[first]["status"] = "ready"
    result = await admission.pass_once()
    assert result.released == [first]
    assert result.started == [second]


async def test_tasks_on_separate_areas_run_together(repo):
    tasks = Tasks()
    a, b = tasks.add(repo, paths=["src/x.py"]), tasks.add(repo, paths=["docs/y.md"])
    admission, started = _admission(tasks)
    assert sorted((await admission.pass_once()).started) == sorted([a, b])


async def test_concurrency_limit(repo):
    tasks = Tasks()
    ids = [tasks.add(f"{repo}-{n}") for n in range(3)]
    admission, started = _admission(tasks, max_running=2)
    assert (await admission.pass_once()).started == ids[:2]


async def test_a_paused_or_spent_goal_holds_its_tasks(repo):
    tasks, goals = Tasks(), Goals()
    goals.goals["g-paused"] = {"status": "paused", "max_usd": None}
    goals.goals["g-spent"] = {"status": "active", "max_usd": "1.00"}
    done = tasks.add(f"{repo}-done", status="ready", goal_id="g-spent")
    paused = tasks.add(f"{repo}-a", goal_id="g-paused")
    spent = tasks.add(f"{repo}-b", goal_id="g-spent")
    admission, started = _admission(tasks, goals, Events(spent={done: [Decimal("0.60"), Decimal("0.45")]}))
    result = await admission.pass_once()
    assert result.started == []
    assert "paused" in result.held[paused]
    assert "spent $1.05" in result.held[spent]


async def test_the_kill_switch_admits_nothing_and_stops_running_tasks(repo):
    from meta_harness.scheduler.admission import set_kill_switch

    tasks = Tasks()
    running, queued = tasks.add(repo, status="running"), tasks.add(f"{repo}-2")
    canceled = []

    async def cancel(task_id):
        canceled.append(task_id)

    admission, started = _admission(tasks)
    admission.cancel = cancel
    set_kill_switch(True, "test", "stop everything")
    try:
        result = await admission.pass_once()
    finally:
        set_kill_switch(False, "test", "lifted")
    assert started == []
    assert canceled == [running]
    assert queued not in result.started


async def test_a_cancel_request_stops_that_running_task(repo):
    tasks = Tasks()
    task_id = tasks.add(repo, status="running")
    tasks.tasks[task_id]["cancel_requested"] = True
    canceled = []

    async def cancel(task_id):
        canceled.append(task_id)

    admission, _ = _admission(tasks)
    admission.cancel = cancel
    await admission.pass_once()
    await admission.pass_once()
    assert canceled == [task_id]


async def test_each_operator_message_is_signaled_to_its_task_once_and_a_failed_one_waits(repo):
    tasks = Tasks()
    tasks.messages = [
        {"id": 1, "task_id": "t-a", "body": "Use the helper.", "deliver": "next_turn"},
        {"id": 2, "task_id": "t-b", "body": "", "deliver": "stop"},
    ]
    tasks.delivered = []
    tasks.pending_messages = lambda: [m for m in tasks.messages if m["id"] not in tasks.delivered]
    tasks.mark_delivered = tasks.delivered.append
    signaled = []

    async def signal(task_id, message):
        if task_id == "t-b" and not signaled:
            signaled.append(("failed", task_id))
            raise RuntimeError("no workflow yet")
        signaled.append((task_id, message))

    admission = Admission(tasks, None, Events(), lambda task_id: None, 5, signal=signal)
    await admission.pass_once()
    await admission.pass_once()

    assert tasks.delivered == [1, 2]
    assert (
        signaled[1] == ("t-a", {"message_id": 1, "text": "Use the helper.", "when": "next_turn"})
        or ("t-a", {"message_id": 1, "text": "Use the helper.", "when": "next_turn"}) in signaled
    )
    assert ("t-b", {"message_id": 2, "text": "", "when": "stop"}) in signaled
