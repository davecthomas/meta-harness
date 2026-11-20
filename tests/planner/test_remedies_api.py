"""The supervisor's remedies as the planner serves them (GET /v1/supervisor/remedies)."""

from fastapi.testclient import TestClient

from meta_harness import db
from meta_harness.supervisor import ledger

from .test_planner import OPERATOR, Events, FakeTasks, FakeTracker, _no_title, migrated  # noqa: F401


def test_the_ledger_is_served_for_a_task_or_a_goal(monkeypatch):
    from meta_harness.planner.service import create_app

    with db.connect(autocommit=True) as conn:
        conn.execute("TRUNCATE supervisor.remedy")
    ledger.write(
        {
            "task_id": "t-1",
            "goal_id": "g-1",
            "situation": "retry_due",
            "remedy": "retry",
            "try": 1,
            "of": 3,
            "outcome": "started",
            "because": "it failed on the system's side",
            "did": "ran it again",
            "next": "it starts from the queue",
            "error": None,
        }
    )
    app = create_app(
        Events(),
        FakeTracker(),
        FakeTasks(),
        operator_keys={"op-key": "dave"},
        service_keys={},
        sweep_seconds=0,
        titler=_no_title,
    )
    with TestClient(app) as client:
        found = client.get("/v1/supervisor/remedies", params={"goal_id": "g-1"}, headers=OPERATOR).json()
    assert [(r["task_id"], r["did"]) for r in found["remedies"]] == [("t-1", "ran it again")]
