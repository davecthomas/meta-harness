"""`mh goal plan`: a goal's plan, its tasks in run order and its gaps, read through a fake planner client."""

import httpx
import pytest
from click.testing import CliRunner

from meta_harness import cli
from meta_harness.planner import client as planner_client
from meta_harness.tasks.client import TaskApiError

CHECKS = "Do it.\n\n## Acceptance checks\n\n- `make test`\n"


def _draft(draft_id: str, title: str, depends_on: list[str] | None = None) -> dict:
    return {
        "draft_id": draft_id,
        "repo": "acme/app",
        "title": title,
        "body": CHECKS,
        "labels": [],
        "depends_on": depends_on or [],
    }


def _plan(**overrides) -> dict:
    plan = {
        "version": 3,
        "summary": "Move the store to Postgres, then retire the old cache.",
        "gaps": ["No load test of the new store", "The admin page is not covered"],
        # Listed out of dependency order: api waits on schema, which waits on nothing.
        "drafts": [
            _draft("api", "Serve reads from Postgres", ["schema"]),
            _draft("schema", "Add the Postgres schema"),
        ],
        "items": [
            {"ref": "acme/app#7", "decision": "keep", "reason": "", "depends_on": ["api"], "replaced_by": []},
            {
                "ref": "acme/app#8",
                "decision": "split",
                "reason": "too big for one task",
                "depends_on": [],
                "replaced_by": ["schema", "api"],
            },
            {
                "ref": "acme/app#9",
                "decision": "leave_out",
                "reason": "the cache is retired anyway",
                "depends_on": [],
                "replaced_by": [],
            },
        ],
        "issues": [
            {"ref": "acme/app#7", "repo": "acme/app", "title": "Drop the cache flag"},
            {"ref": "acme/app#8", "repo": "acme/app", "title": "Move to Postgres"},
            {"ref": "acme/app#9", "repo": "acme/app", "title": "Tune the cache"},
        ],
        "invalid": [],
    }
    plan.update(overrides)
    return plan


def _shown(
    plan: dict | None,
    approved_at: str | None = None,
    planning_task_id: str | None = None,
    status: str = "planning",
) -> dict:
    return {
        "goal_id": "g-1",
        "status": status,
        "planning_task_id": planning_task_id,
        "approved_at": approved_at,
        "label": "mh:g-1",
        "plan": plan,
    }


class FakeGoals:
    response: dict | Exception = {}
    asked: list[str] = []

    def plan(self, goal_id: str) -> dict:
        FakeGoals.asked.append(goal_id)
        if isinstance(FakeGoals.response, Exception):
            raise FakeGoals.response
        return FakeGoals.response


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(planner_client, "GoalsClient", FakeGoals)
    FakeGoals.asked = []

    def invoke(response):
        FakeGoals.response = response
        return CliRunner().invoke(cli.main, ["goal", "plan", "g-1"])

    return invoke


def test_tasks_print_in_run_order(run):
    result = run(_shown(_plan()))
    assert result.exit_code == 0, result.output
    assert FakeGoals.asked == ["g-1"]
    task_lines = [line for line in result.output.splitlines() if line.startswith("  ") and ". " in line[:6]]
    assert [line.split()[1] for line in task_lines] == ["schema", "api", "acme/app#7"]
    assert "Add the Postgres schema" in task_lines[0] and "waits on nothing" in task_lines[0]
    assert "Serve reads from Postgres" in task_lines[1] and "waits on schema" in task_lines[1]
    assert "Drop the cache flag" in task_lines[2] and "waits on api" in task_lines[2]
    assert all("acme/app " in line for line in task_lines)


def test_summary_gaps_and_dropped_issues_appear(run):
    result = run(_shown(_plan()))
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Move the store to Postgres, then retire the old cache." in out
    assert "No load test of the new store" in out
    assert "The admin page is not covered" in out
    assert "acme/app#8  split: too big for one task (replaced by schema, api)" in out
    assert "acme/app#9  leave_out: the cache is retired anyway" in out
    assert "Cannot be approved" not in out


def test_no_gaps_and_invalid_reasons(run):
    result = run(_shown(_plan(gaps=[], invalid=["api: needs a title"])))
    assert result.exit_code == 0, result.output
    assert "Gaps:\n  none" in result.output
    assert "Cannot be approved:\n  - api: needs a title" in result.output


def test_approved_and_awaiting_headings_differ(run):
    approved = run(_shown(_plan(), approved_at="2026-05-01T10:00:00+00:00", status="active"))
    awaiting = run(_shown(_plan(), planning_task_id="t-42"))
    assert approved.exit_code == 0 and awaiting.exit_code == 0
    approved_head = approved.output.splitlines()[0]
    awaiting_head = awaiting.output.splitlines()[0]
    assert approved_head != awaiting_head
    assert "v3" in approved_head and "active" in approved_head
    assert "approved 2026-05-01T10:00:00+00:00" in approved_head
    assert "awaiting approval" in awaiting_head
    assert "still drafting" in awaiting.output and "t-42" in awaiting.output
    assert "still drafting" not in approved.output


def test_replanned_goal_awaits_approval_despite_old_approval(run):
    result = run(_shown(_plan(), approved_at="2026-05-01T10:00:00+00:00"))
    assert result.exit_code == 0, result.output
    head = result.output.splitlines()[0]
    assert "planning" in head and "awaiting approval" in head
    assert "approved 2026" not in head


def test_goal_with_no_plan(run):
    result = run(_shown(None))
    assert result.exit_code == 0, result.output
    assert result.output == "g-1 has no plan\n"


def test_api_error_exits_non_zero(run):
    result = run(TaskApiError(404, "no goal g-1"))
    assert result.exit_code != 0
    assert "no goal g-1" in result.output


def test_client_plan_requests_the_goal_plan():
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json=_shown(None))

    goals = planner_client.GoalsClient(base_url="http://planner.test", api_key="k")
    goals.http = httpx.Client(base_url="http://planner.test", transport=httpx.MockTransport(handler))
    assert goals.plan("g-1") == _shown(None)
    assert seen == [("GET", "/v1/goals/g-1/plan")]
