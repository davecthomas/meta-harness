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


def test_no_task_runs_without_a_spend_limit_and_none_above_the_ceiling(monkeypatch):
    from decimal import Decimal

    from meta_harness.config import store as config_store
    from meta_harness.run_service.shared import DEFAULT_SPEND_LIMIT_USD, TASK_SPEND_CEILING_USD

    monkeypatch.setattr(config_store, "task_spend_ceiling", lambda: Decimal("100"))
    monkeypatch.setattr(config_store, "budget_policy", lambda fresh=False: "block")
    assert task_activities._spend_limit({"max_usd": "25.00"}) == "25.00"
    assert task_activities._spend_limit({"max_usd": None}) == DEFAULT_SPEND_LIMIT_USD
    assert task_activities._spend_limit({"max_usd": "5000.00"}) == "100.00", "a limit above the ceiling is the ceiling"

    # Under track a task's own limit does not stop it, so the ceiling is its limit: never an open one.
    monkeypatch.setattr(config_store, "budget_policy", lambda fresh=False: "track")
    assert task_activities._spend_limit({"max_usd": "25.00"}) == "100.00"
    assert task_activities._spend_limit({"max_usd": None}) == "100.00"
    monkeypatch.setattr(config_store, "task_spend_ceiling", lambda: Decimal("40"))
    assert task_activities._spend_limit({"max_usd": None}) == "40.00"
    assert TASK_SPEND_CEILING_USD == "100.00"


def test_the_ceiling_is_the_policys_or_the_default_and_never_opens_on_a_bad_read(monkeypatch):
    from decimal import Decimal

    from meta_harness.config import store as config_store

    def policy(document):
        monkeypatch.setattr(config_store, "current_document", lambda kind: document)

    policy({"max_task_usd": "250.00"})
    assert config_store.task_spend_ceiling() == Decimal("250.00")
    for document in (
        {},
        {"max_task_usd": None},
        {"max_task_usd": "lots"},
        {"max_task_usd": "-5"},
        {"max_task_usd": "Infinity"},
    ):
        policy(document)
        assert config_store.task_spend_ceiling() == Decimal("100.00"), document

    def unreadable(kind):
        raise RuntimeError("no database")

    monkeypatch.setattr(config_store, "current_document", unreadable)
    assert config_store.task_spend_ceiling() == Decimal("100.00")
