"""The run service's records on the task: a pull request is saved on the task the moment it opens."""

from temporalio.testing import ActivityEnvironment

from meta_harness.run_service import task_activities
from meta_harness.run_service.shared import PublishedRecord, PublishResult


async def test_an_opened_pull_request_is_saved_on_the_running_task_before_its_event(monkeypatch):
    calls = []

    class Tasks:
        def set_status(self, task_id, status, reason=None, **fields):
            calls.append(("set_status", task_id, status, fields))
            return {"id": task_id, "status": status}

    monkeypatch.setattr(task_activities, "_tasks", lambda: Tasks())
    monkeypatch.setattr(
        task_activities.events, "emit", lambda kind, payload, **fields: calls.append(("emit", kind, payload["pr_url"]))
    )
    published = PublishResult(
        branch="mh/t-1",
        base="main",
        commit="abc",
        files=["README.md"],
        pr_url="https://github.com/o/r/pull/9",
        created=True,
    )

    await ActivityEnvironment().run(task_activities.record_published, PublishedRecord("t-1", published))

    assert calls == [
        ("set_status", "t-1", "running", {"pr_url": "https://github.com/o/r/pull/9", "head_sha": "abc"}),
        ("emit", "pull_request_opened", "https://github.com/o/r/pull/9"),
    ]


def test_a_tasks_spend_limit_is_out_of_reach_while_the_budget_policy_only_tracks(monkeypatch):
    from meta_harness.config import store as config_store
    from meta_harness.run_service.shared import DEFAULT_SPEND_LIMIT_USD, UNBOUNDED_SPEND_LIMIT_USD

    monkeypatch.setattr(config_store, "budget_policy", lambda fresh=False: "block")
    assert task_activities._spend_limit({"max_usd": "25.00"}) == "25.00"
    assert task_activities._spend_limit({"max_usd": None}) == DEFAULT_SPEND_LIMIT_USD

    monkeypatch.setattr(config_store, "budget_policy", lambda fresh=False: "track")
    assert task_activities._spend_limit({"max_usd": "25.00"}) == UNBOUNDED_SPEND_LIMIT_USD
