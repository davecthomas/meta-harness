# Created by the meta-harness in run t-b8570988a7df:run-1 on harness:pi-lean/claude:claude-sonnet-5-5.

"""The plan view returns one planning run's limit, for the answer button's label."""

from unittest.mock import patch

from meta_harness.planner import plan_api, planning

from .test_planner import OPERATOR, migrated, registry  # noqa: F401
from .test_planning import _feature
from .test_planning import world  # noqa: F401


def test_the_plan_view_returns_the_planning_limit(world):  # noqa: F811
    client, _, _, _ = world
    goal = _feature(client)

    found = client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()

    assert found["planning_usd"] == plan_api._planning_usd()
    assert found["planning_usd"] == str(planning.settings()["max_usd"])


def test_the_plan_view_returns_null_when_no_limit_is_set(world):  # noqa: F811
    client, _, _, _ = world
    goal = _feature(client)

    with patch.object(planning, "settings", side_effect=RuntimeError("no limit")):
        found = client.get(f"/v1/goals/{goal['id']}/plan", headers=OPERATOR).json()

    assert found["planning_usd"] is None
