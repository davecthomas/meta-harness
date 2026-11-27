# Created by the meta-harness in run t-4d0438b23b7a:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""An operator says one of a plan's open questions (its gaps) is not a problem.

The planner's API runs against Postgres with fakes for the tracker, the Task API and the event service.
"""

import jsonschema
import pytest
from fastapi.testclient import TestClient

from meta_harness import db
from meta_harness.events import types
from meta_harness.planner import plans
from meta_harness.planner.service import create_app

from .test_planner import OPERATOR, Events, FakeTasks, _no_title, migrated, registry  # noqa: F401
from .test_planning import WritingTracker, _feature, draft

CLASH = "Store and api both edit app.py, so they may clash."
OPEN = "How many exports a day the team expects is not known."


@pytest.fixture
def world():
    events, tracker, tasks = Events(), WritingTracker(), FakeTasks()
    app = create_app(
        events, tracker, tasks, operator_keys={"op-key": "dave"}, service_keys={}, sweep_seconds=0, titler=_no_title
    )
    with TestClient(app) as client:
        yield client, events, tasks


def _planned(client, events, tasks, gaps=(CLASH, OPEN)) -> tuple[dict, dict]:
    """A goal in planning whose planning task ended with two tasks and these gaps."""
    goal = _feature(client)
    task = next(t for t in tasks.tasks.values() if t.get("goal_id") == goal["id"])
    for d in (draft("store"), draft("api")):
        events.emit("issue_drafted", d, task_id=task["id"])
    events.emit("plan_summarized", {"summary": "Store, then api.", "gaps": list(gaps)}, task_id=task["id"])
    task["status"] = "closed"
    shown = client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()
    return goal, shown


def _dismiss(client, goal_id: str, version: int, gap: str):
    return client.post(
        f"/v1/goals/{goal_id}/plan/gaps/dismiss", headers=OPERATOR, json={"version": version, "gap": gap}
    )


def test_dismissing_a_gap_takes_it_out_of_the_version_and_records_who(world):
    client, events, tasks = world
    goal, before = _planned(client, events, tasks)
    assert before["plan"]["gaps"] == [CLASH, OPEN] and before["glance"]["open_questions"] == 2

    answer = _dismiss(client, goal["id"], 1, CLASH)

    assert answer.status_code == 200, answer.text
    shown = answer.json()
    assert shown["plan"]["version"] == 1, "the version is changed in place, not saved again"
    assert shown["plan"]["gaps"] == [OPEN]
    assert shown["glance"]["open_questions"] == 1
    [dismissed] = shown["plan"]["dismissed_gaps"]
    assert dismissed["gap"] == CLASH and dismissed["by"] == "dave" and dismissed["at"]
    stored = [e for e in events.stored.values() if e["event_type"] == "plan_gap_dismissed"]
    assert len(stored) == 1
    event = stored[0]
    assert event["payload"] == {"goal_id": goal["id"], "version": 1, "gap": CLASH}
    assert event["actor"] == "dave" and event["tags"] == {"goal_id": goal["id"]}
    schema = next(t.json_schema for t in types.REGISTRY if t.name == "plan_gap_dismissed")
    jsonschema.validate(event["payload"], schema)


def test_a_dismissed_gap_stays_out_when_the_plan_is_read_again(world):
    client, events, tasks = world
    goal, _ = _planned(client, events, tasks)
    assert _dismiss(client, goal["id"], 1, OPEN).status_code == 200

    again = client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()

    assert again["plan"]["gaps"] == [CLASH] and again["glance"]["open_questions"] == 1
    assert [d["gap"] for d in again["plan"]["dismissed_gaps"]] == [OPEN]
    with db.connect(autocommit=True) as conn:
        assert plans.at(conn, goal["id"], 1)["gaps"] == [CLASH]
    # The last one goes too, and the list is empty.
    assert _dismiss(client, goal["id"], 1, CLASH).json()["glance"]["open_questions"] == 0


def test_a_gap_the_version_does_not_hold_is_a_404(world):
    client, events, tasks = world
    goal, _ = _planned(client, events, tasks)

    missing = _dismiss(client, goal["id"], 1, "Nobody asked about this.")
    assert missing.status_code == 404 and "Reload the plan" in missing.json()["detail"]

    assert _dismiss(client, goal["id"], 1, CLASH).status_code == 200
    twice = _dismiss(client, goal["id"], 1, CLASH)
    assert twice.status_code == 404, "a gap dismissed already is no longer the version's"
    assert events.types().count("plan_gap_dismissed") == 1


def test_an_older_version_or_a_goal_not_in_planning_is_a_409(world):
    client, events, tasks = world
    goal, _ = _planned(client, events, tasks)
    edited = client.put(
        f"/v1/goals/{goal['id']}/plan", headers=OPERATOR, json={"drafts": [draft("store"), draft("api")]}
    )
    assert edited.status_code == 200 and edited.json()["plan"]["version"] == 2

    older = _dismiss(client, goal["id"], 1, CLASH)
    assert older.status_code == 409 and "not the plan's newest" in older.json()["detail"]
    later = _dismiss(client, goal["id"], 7, CLASH)
    assert later.status_code == 409

    approved = client.post(f"/v1/goals/{goal['id']}/approve", headers=OPERATOR, json={"track": True})
    assert approved.status_code == 200, approved.text
    active = _dismiss(client, goal["id"], 2, CLASH)
    assert active.status_code == 409 and "no plan to change" in active.json()["detail"]
    assert "plan_gap_dismissed" not in events.types()
