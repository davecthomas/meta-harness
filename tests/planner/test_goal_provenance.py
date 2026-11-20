"""A goal's provenance setting: whether the files its runs create start with a header naming the run. Unset (null)
leaves it to config/provenance.json."""

import pytest

from .test_planner import OPERATOR, _label, api, migrated, parts, registry  # noqa: F401

pytestmark = pytest.mark.db
REPO = "davecthomas/meta-harness"


def _goal(api, **fields) -> dict:  # noqa: F811
    created = api.post(
        "/v1/goals", json={"statement": "Fix bugs", "repos": [REPO], "label": _label(), **fields}, headers=OPERATOR
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_a_goal_leaves_provenance_to_the_configuration_unless_it_says(api):  # noqa: F811
    assert _goal(api)["provenance"] is None
    assert _goal(api, provenance=False)["provenance"] is False


def test_a_goal_s_provenance_can_be_turned_on_off_and_back_to_the_configuration(api, parts):  # noqa: F811
    events = parts[0]
    goal = _goal(api)
    url = f"/v1/goals/{goal['id']}"

    for value in (True, False, None):
        changed = api.patch(url, json={"provenance": value}, headers=OPERATOR)
        assert changed.status_code == 200, changed.text
        assert changed.json()["provenance"] is value
        assert api.get(url, headers=OPERATOR).json()["provenance"] is value

    changes = [p["changes"] for t, p in events.events if t == "goal_changed" and "provenance" in p.get("changes", {})]
    assert [c["provenance"]["to"] for c in changes] == ["True", "False", "None"]
