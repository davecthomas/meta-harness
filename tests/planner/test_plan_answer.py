# Created by the meta-harness in run t-cfbb1bd5ec61:run-1 on harness:pi-lean/claude:claude-sonnet-5-5.

"""An operator's answer to a plan's question plans again, and the planning run's note names the question."""

from .test_planner import OPERATOR, migrated, registry  # noqa: F401
from .test_planning import _feature, draft, world  # noqa: F401

ITEM = "Nothing covers export."
ANSWER = "Export is out of scope; CSV only."


def test_an_answer_to_a_gap_starts_a_planning_run_whose_note_names_the_item_and_the_answer(world):  # noqa: F811
    client, events, _, tasks = world
    goal = _feature(client)
    first = next(t for t in tasks.tasks.values() if t.get("goal_id") == goal["id"])
    events.emit("issue_drafted", draft("store"), task_id=first["id"])
    events.emit("plan_summarized", {"summary": "Store.", "gaps": [ITEM]}, task_id=first["id"])
    first["status"] = "closed"
    shown = client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()
    assert shown["plan"]["gaps"] == [ITEM]

    sent = client.post(f"/v1/goals/{goal['id']}/replan", json={"note": f"About {ITEM}: {ANSWER}"}, headers=OPERATOR)

    assert sent.status_code == 202, sent.text
    second = tasks.tasks[sent.json()["planning_task_id"]]
    assert second["id"] != first["id"] and second["task_type"] == "plan" and second["goal_id"] == goal["id"]
    assert f"About {ITEM}: {ANSWER}" in second["description"]
    assert client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["status"] == "planning"
