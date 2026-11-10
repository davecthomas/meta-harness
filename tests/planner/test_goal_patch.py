"""PATCH /v1/goals/{id} without Postgres: which fields a patch sends to the store, and what it refuses first."""

import contextlib
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from meta_harness.planner import service as planner_service
from meta_harness.planner import store, sweep
from meta_harness.planner.service import create_app

OPERATOR = {"Authorization": "Bearer op-key"}
REPO = "davecthomas/meta-harness"


class Events:
    def __init__(self):
        self.events = []

    def emit(self, event_type, payload=None, **fields):
        self.events.append((event_type, payload or {}))


class Pool:
    @contextlib.contextmanager
    def connection(self):
        yield None

    def close(self):
        pass


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    """The example repo registry, whatever the machine has."""
    from meta_harness.planner import repos
    from meta_harness.run_service.paths import REPO_ROOT

    monkeypatch.setenv("MH_REPOS_CONFIG", str(REPO_ROOT / "config" / "repos.example.json"))
    monkeypatch.delenv("MH_CONFIG_SOURCE", raising=False)
    repos._file_registry.cache_clear()
    yield
    repos._file_registry.cache_clear()


@pytest.fixture
def patched(monkeypatch):
    """The planner over a store whose update records the fields it is given and changes them all."""
    sent: list[dict] = []
    goal = {"id": "g-1", "label": "bug", "repos": [REPO], "max_usd": Decimal("10"), "max_parallel": 2, "priority": 0}

    def update(conn, goal_id, only_if=None, dropped=None, **fields):
        sent.append(fields)
        before = {k: goal.get(k) for k in fields}
        if "repos" in fields and dropped is not None:
            # An issue with no task yet, held in a repo the goal no longer names.
            dropped.extend(f"github:{r}#1" for r in goal["repos"] if r not in fields["repos"])
        goal.update(fields)
        return dict(goal), before

    monkeypatch.setattr(planner_service.db, "pool", lambda url=None: Pool())
    monkeypatch.setattr(store, "update", update)
    monkeypatch.setattr(planner_service, "system_max_parallel", lambda: 4)
    events = Events()
    app = create_app(events, None, None, operator_keys={"op-key": "dave"}, service_keys={}, sweep_seconds=0)
    with TestClient(app) as client:
        yield client, sent, events


def test_a_patch_sends_only_the_fields_given_and_clears_those_sent_as_null(patched):
    client, sent, events = patched

    assert client.patch("/v1/goals/g-1", json={"max_usd": None}, headers=OPERATOR).status_code == 200
    assert client.patch("/v1/goals/g-1", json={"label": None}, headers=OPERATOR).status_code == 200
    assert client.patch("/v1/goals/g-1", json={"title": None, "priority": 3}, headers=OPERATOR).status_code == 200

    assert sent == [{"max_usd": None}, {"label": None}, {"priority": 3}]
    changes = [p["changes"] for t, p in events.events if t == "goal_changed"]
    assert changes[0] == {"max_usd": {"from": "10", "to": "None"}}


def test_a_patch_takes_a_label_and_registered_repos_by_their_registry_names(patched):
    client, sent, _ = patched
    other = "davecthomas/ai-api-unified"

    changed = client.patch(
        "/v1/goals/g-1", json={"label": "mh:next", "repos": [REPO, other.upper(), other]}, headers=OPERATOR
    )

    assert changed.status_code == 200, changed.text
    assert sent == [{"label": "mh:next", "repos": [REPO, other]}]


def test_a_change_of_repos_records_each_issue_it_let_go(patched):
    client, _, events = patched
    other = "davecthomas/ai-api-unified"

    assert client.patch("/v1/goals/g-1", json={"repos": [other]}, headers=OPERATOR).status_code == 200

    assert ("issue_dropped", {"ref": f"github:{REPO}#1", "goal_id": "g-1"}) in events.events


async def test_a_sweep_makes_no_task_for_a_picked_issue_outside_the_goals_repos(monkeypatch):
    other = "davecthomas/ai-api-unified"
    links = [
        {"ref": f"github:{other}#5", "repo": other, "task_id": None, "refused_reason": None, "state": "open"},
        {"ref": f"github:{REPO}#6", "repo": REPO, "task_id": None, "refused_reason": None, "state": "open"},
    ]
    read: list[str] = []
    taken: list[str] = []

    class Tracker:
        async def read(self, ref):
            read.append(ref)
            return SimpleNamespace(ref=ref, state="open")

    async def take_in(goal, issue, *args):
        taken.append(issue.ref)

    monkeypatch.setattr(sweep, "_links", lambda goal_id: links)
    monkeypatch.setattr(sweep, "_take_in", take_in)
    goal = {"id": "g-1", "repos": [REPO.upper()]}

    await sweep._take_in_added(goal, set(), Tracker(), None, None, sweep.SweepResult("g-1"))

    assert read == taken == [f"github:{REPO}#6"]


@pytest.mark.parametrize(
    "body",
    [
        {"repos": []},
        {"repos": [REPO, "o/unknown"]},
        {"label": ""},
        {"max_usd": "0"},
        {"max_parallel": 5},
    ],
)
def test_a_patch_the_planner_refuses_reaches_no_store(patched, body):
    client, sent, _ = patched
    assert client.patch("/v1/goals/g-1", json=body, headers=OPERATOR).status_code == 422
    assert sent == []
