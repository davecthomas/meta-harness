"""The planner watching the pull requests of tasks that ended ready or review_unresolved, without Postgres."""

import pytest

from meta_harness.events.client import stable_event_id
from meta_harness.events.types import by_key
from meta_harness.planner import progress, sweep
from meta_harness.run_service import code_host, events as run_events
from meta_harness.run_service.code_host import CheckRun, CodeHostError, PullRequest
from meta_harness.tasks.client import TaskApiError

GOAL = "g-prs"
REPO = "davecthomas/meta-harness"


class Events:
    def __init__(self):
        self.events = []

    def emit(self, event_type, payload=None, **fields):
        self.events.append({"event_type": event_type, "payload": payload or {}, **fields})
        return fields.get("event_id", "id")


class Tasks:
    def __init__(self, *tasks):
        self.tasks = {t["id"]: dict(t) for t in tasks}
        self.moves = []

    def list(self, statuses=None, goal_id=None, limit=100):
        assert goal_id == GOAL
        return [t for t in self.tasks.values() if not statuses or t["status"] in statuses][:limit]

    def page(self, statuses=None, goal_id=None, limit=25, offset=0):
        found = self.list(statuses, goal_id, len(self.tasks))
        return {"tasks": found[offset : offset + limit], "total": len(found), "offset": offset, "limit": limit}

    def set_status(self, task_id, status, reason=None, **fields):
        self.moves.append((task_id, status, reason))
        self.tasks[task_id]["status"] = status
        return self.tasks[task_id]


@pytest.fixture(autouse=True)
def ends(monkeypatch):
    """The planner's record of the pull request ends a sweep recorded, in memory: {task id: outcome}."""
    saved: dict[str, str] = {}
    monkeypatch.setattr(sweep, "_save_end", lambda task_id, state: saved.setdefault(task_id, state))
    monkeypatch.setattr(sweep, "_ended", lambda task_ids: {t for t in task_ids if t in saved})
    return saved


class Host:
    def __init__(self, pulls: dict[int, PullRequest], runs: dict[str, list[CheckRun]] | None = None):
        self.pulls = pulls
        self.runs = runs or {}
        self.reads = []

    async def read_pull_request(self, repo, number):
        self.reads.append((repo, number))
        if number not in self.pulls:
            raise CodeHostError("GitHub answered 404", retryable=False, status=404)
        return self.pulls[number]

    async def check_runs(self, repo, sha):
        return self.runs.get(sha, [])


def _task(task_id: str, number: int, status: str = "ready") -> dict:
    return {
        "id": task_id,
        "goal_id": GOAL,
        "status": status,
        "pr_url": f"https://github.com/{REPO}/pull/{number}",
        "head_sha": "first",
    }


def _pull(number: int, state: str, head: str = "abc123", merge: str = "") -> PullRequest:
    return PullRequest(REPO, number, f"https://github.com/{REPO}/pull/{number}", state, head, merge)


def _run(name: str, status: str = "completed", conclusion: str | None = "success") -> CheckRun:
    return CheckRun(name=name, status=status, conclusion=conclusion, summary="")


async def _watch(tasks, host, events=None):
    events = events or Events()
    result = sweep.SweepResult(GOAL)
    await sweep._watch_pull_requests({"id": GOAL}, tasks, events, host, result)
    return result, events


async def test_a_merged_pull_request_closes_its_task_with_the_workflows_event_id():
    tasks = Tasks(_task("t-1", 11))
    result, events = await _watch(tasks, Host({11: _pull(11, "merged", merge="m1")}))

    assert tasks.moves == [("t-1", "closed", "its pull request merged")]
    assert result.merged == ["t-1"] and not result.errors
    [event] = events.events
    assert event["event_type"] == "pull_request_merged"
    assert event["payload"] == {"pr_url": f"https://github.com/{REPO}/pull/11", "head_sha": "abc123", "merge_sha": "m1"}
    assert event["task_id"] == "t-1" and event["tags"] == {"goal_id": GOAL}
    # One fact, one event, whoever saw it: the RunTask workflow's merge activity makes the same id.
    assert event["event_id"] == run_events.stable_id("t-1", "pull_request_merged")


async def test_a_pull_request_closed_without_merging_cancels_its_task():
    tasks = Tasks(_task("t-2", 12, status="review_unresolved"))
    result, events = await _watch(tasks, Host({12: _pull(12, "closed")}))

    assert tasks.moves == [("t-2", "canceled", "its pull request was closed without merging")]
    assert result.canceled == ["t-2"]
    [event] = events.events
    assert event["event_type"] == "pull_request_closed"
    assert event["event_id"] == stable_event_id("t-2", "pull_request_closed")


async def _watch_canceled(tasks, host, events):
    result = sweep.SweepResult(GOAL)
    await sweep._watch_canceled_pull_requests({"id": GOAL}, tasks, events, host, result)
    return result


async def test_a_canceled_tasks_pull_request_merged_or_closed_later_is_recorded_once_and_the_task_stays_canceled(
    ends,
):
    from meta_harness.planner import cost_facts

    class Reader(Events):
        def read(self, from_position=0, limit=500, before_position=None, **filters):
            types = filters["event_type"].split(",")
            found = [{**e, "position": n} for n, e in enumerate(self.events, 1) if e["event_type"] in types]
            return {"events": [e for e in found if e["position"] > from_position][:limit]}

    tasks = Tasks(_task("t-m", 21, "canceled"), _task("t-c", 22, "canceled"), _task("t-q", 23, "queued"))
    host = Host({21: _pull(21, "open"), 22: _pull(22, "draft"), 23: _pull(23, "merged")})
    events = Reader()
    first = await _watch_canceled(tasks, host, events)
    assert first.ended == [] and events.events == [], "still open: nothing ended yet"
    assert sorted(host.reads) == [(REPO, 21), (REPO, 22)], "only the canceled tasks' pull requests"
    facts = {
        t["id"]: cost_facts.outcome(t, cost_facts.pull_request_ends(events).get(t["id"])) for t in tasks.tasks.values()
    }
    assert facts["t-m"] == facts["t-c"] == "open"

    # An operator merges one by hand, and closes the other without merging.
    host.pulls[21] = _pull(21, "merged", merge="m21")
    host.pulls[22] = _pull(22, "closed")
    later = await _watch_canceled(tasks, host, events)

    assert sorted(later.ended) == ["t-c", "t-m"] and not later.errors
    by_task = {e["task_id"]: e for e in events.events}
    assert by_task["t-m"]["event_type"] == "pull_request_merged"
    assert by_task["t-m"]["event_id"] == run_events.stable_id("t-m", "pull_request_merged")
    assert by_task["t-c"]["event_type"] == "pull_request_closed"
    assert tasks.moves == [] and tasks.tasks["t-m"]["status"] == "canceled", "the task stays canceled"
    assert ends == {"t-m": "merged", "t-c": "closed"}
    recorded = cost_facts.pull_request_ends(events)
    assert cost_facts.outcome(tasks.tasks["t-m"], recorded.get("t-m")) == "merged"
    assert cost_facts.outcome(tasks.tasks["t-c"], recorded.get("t-c")) == "closed"

    reads = len(host.reads)
    again = await _watch_canceled(tasks, host, events)
    assert again.ended == [] and len(host.reads) == reads, "an ended pull request is not read again"


async def test_a_pull_request_the_watch_ended_is_not_read_again_once_its_task_is_canceled(ends):
    tasks = Tasks(_task("t-8", 18, status="review_unresolved"))
    host = Host({18: _pull(18, "closed")})
    events = Events()
    await _watch(tasks, host, events)
    assert tasks.tasks["t-8"]["status"] == "canceled" and ends == {"t-8": "closed"}

    result = await _watch_canceled(tasks, host, events)
    assert result.ended == [] and host.reads == [(REPO, 18)] and len(events.events) == 1


async def test_settled_checks_on_a_new_head_are_recorded_once_per_head():
    tasks = Tasks(_task("t-3", 13))
    host = Host({13: _pull(13, "open", head="h2")}, {"h2": [_run("lint"), _run("tests", conclusion="failure")]})
    events = Events()
    await _watch(tasks, host, events)
    await _watch(tasks, host, events)

    assert tasks.moves == []
    assert [e["event_type"] for e in events.events] == ["pull_request_checks", "pull_request_checks"]
    first, second = events.events
    assert first["payload"] == {"head_sha": "h2", "state": "failed", "failed": ["tests"]}
    # A repeated sweep sends the same event id, so the event service keeps one.
    assert first["event_id"] == second["event_id"] == run_events.stable_id("t-3", "pull_request_checks", "h2")


@pytest.mark.parametrize("runs", [[], [_run("tests", status="in_progress", conclusion=None)]])
async def test_pending_or_missing_checks_record_nothing_yet(runs):
    tasks = Tasks(_task("t-4", 14))
    _, events = await _watch(tasks, Host({14: _pull(14, "draft", head="h3")}, {"h3": runs}))
    assert events.events == [] and tasks.moves == []


async def test_only_tasks_with_a_github_pull_request_are_read_and_errors_are_kept():
    local = {**_task("t-5", 15), "pr_url": "file:///runs/x/pull-request.md"}
    none = {**_task("t-6", 16), "pr_url": None}
    tasks = Tasks(local, none, _task("t-7", 17))
    host = Host({})
    result, events = await _watch(tasks, host)

    assert host.reads == [(REPO, 17)]
    assert result.errors and "t-7" in result.errors[0]
    assert events.events == [] and tasks.moves == []


async def test_an_unreachable_task_api_is_an_error_not_a_crash():
    class Down(Tasks):
        def list(self, statuses=None, goal_id=None, limit=100):
            raise TaskApiError(503, "down")

    result, _ = await _watch(Down(), Host({}))
    assert result.errors


def test_the_pull_request_events_are_registered():
    registered = by_key()
    for name in ("pull_request_checks", "pull_request_merged", "pull_request_closed"):
        assert (name, 1) in registered


# --- Reading a pull request and its checks from GitHub ------------------------------------------------


@pytest.mark.parametrize(
    ("pull", "state"),
    [
        ({"state": "open", "draft": False}, "open"),
        ({"state": "open", "draft": True}, "draft"),
        ({"state": "closed", "merged": True, "merge_commit_sha": "m9"}, "merged"),
        ({"state": "closed", "merged": False}, "closed"),
    ],
)
async def test_read_pull_request_reads_its_state_and_head(monkeypatch, pull, state):
    calls = []

    async def github(repo, permissions, method, path, **kwargs):
        calls.append((permissions, method, path))
        return {**pull, "head": {"sha": "h1"}, "html_url": f"https://github.com/{repo}/pull/5"}

    monkeypatch.setattr(code_host, "_github", github)
    found = await code_host.read_pull_request(REPO, 5)

    assert found.state == state and found.head_sha == "h1"
    assert found.merge_sha == ("m9" if state == "merged" else "")
    assert calls == [({"pull_requests": "read"}, "GET", f"/repos/{REPO}/pulls/5")]


def test_checks_state_says_pending_until_every_check_completes():
    assert code_host.checks_state([]).state == "none"
    assert code_host.checks_state([_run("a"), _run("b", "queued", None)]).state == "pending"
    assert code_host.checks_state([_run("a"), _run("b", conclusion="skipped")]).state == "passed"
    failed = code_host.checks_state([_run("a", conclusion="failure"), _run("b", conclusion="timed_out")])
    assert (failed.state, failed.failed) == ("failed", ["a", "b"])


# --- The goal's issues view --------------------------------------------------------------------------


def _event(position, task_id, event_type, **payload):
    return {"position": position, "task_id": task_id, "event_type": event_type, "payload": payload}


def test_each_issue_shows_its_task_status_pull_request_state_and_checks():
    links = [
        {"ref": "github:o/r#1", "repo": "o/r", "state": "open", "task_id": "t-1", "depends_on": []},
        {"ref": "github:o/r#2", "repo": "o/r", "state": "closed", "task_id": "t-2", "depends_on": []},
        {"ref": "github:o/r#3", "repo": "o/r", "state": "open", "task_id": "t-3", "depends_on": []},
        {"ref": "github:o/r#4", "repo": "o/r", "state": "open", "task_id": None, "refused_reason": "no checks"},
    ]
    tasks = [
        {"id": "t-1", "status": "ready", "pr_url": "https://github.com/o/r/pull/9", "head_sha": "h0"},
        {"id": "t-2", "status": "closed", "pr_url": "https://github.com/o/r/pull/10", "head_sha": "h5"},
        {"id": "t-3", "status": "queued", "pr_url": None, "head_sha": None},
    ]
    recorded = [
        _event(1, "t-1", "pull_request_opened", pr_url="https://github.com/o/r/pull/9"),
        _event(2, "t-1", "pull_request_checks", head_sha="h0", state="failed", failed=["tests"]),
        _event(3, "t-1", "pull_request_checks", head_sha="h1", state="passed", failed=[]),
        _event(4, "t-2", "pull_request_checks", head_sha="h5", state="passed", failed=[]),
        _event(5, "t-2", "pull_request_merged", pr_url="https://github.com/o/r/pull/10", head_sha="h5"),
    ]
    shown = {i["ref"]: i for i in progress.issues_progress(links, tasks, recorded)}

    first = shown["github:o/r#1"]
    assert (first["task_status"], first["pr_state"]) == ("ready", "open")
    assert first["checks"] == {"head_sha": "h1", "state": "passed", "failed": []}
    assert (shown["github:o/r#2"]["pr_state"], shown["github:o/r#2"]["task_status"]) == ("merged", "closed")
    assert shown["github:o/r#3"]["pr_url"] is None and shown["github:o/r#3"]["checks"] is None
    assert shown["github:o/r#4"]["task_status"] is None and shown["github:o/r#4"]["refused_reason"] == "no checks"


def test_an_open_pull_request_with_no_recorded_checks_is_pending_and_a_closed_one_says_so():
    task = {"id": "t-1", "status": "ready", "pr_url": "https://github.com/o/r/pull/9", "head_sha": "h0"}
    assert progress.pull_request_view(task, [])["checks"] == {"head_sha": "h0", "state": "pending", "failed": []}
    closed = progress.pull_request_view(task, [_event(1, "t-1", "pull_request_closed", pr_url=task["pr_url"])])
    assert closed["pr_state"] == "closed" and closed["checks"] is None


def test_pull_request_events_are_read_for_the_tasks_page_by_page(monkeypatch):
    monkeypatch.setattr(progress, "EVENTS_PER_READ", 2)
    stored = [_event(n, "t-1", "pull_request_checks", head_sha=f"h{n}", state="passed") for n in range(1, 4)]
    reads = []

    class Reader:
        def read(self, from_position=0, limit=500, before_position=None, **filters):
            reads.append((from_position, filters))
            page = [e for e in stored if e["position"] > from_position][:limit]
            return {"events": page, "next_position": page[-1]["position"] if page else from_position}

    found = progress.pull_request_events(Reader(), ["t-1"])

    assert [e["position"] for e in found] == [1, 2, 3]
    assert reads[0][1] == {"task_id": "t-1", "event_type": ",".join(progress.PR_EVENT_TYPES)}
