"""Remedies: every one the rules can name has a module, and every try is recorded in one fixed shape."""

from datetime import UTC, datetime, timedelta

from meta_harness import db
from meta_harness.supervisor import ledger
from meta_harness.supervisor.remedies import OUTCOMES, registry
from meta_harness.supervisor.rules import ACTIONS, decide

from .test_loop import Events, Host, Tasks, Workflows, migrated  # noqa: F401
from .test_rules import _every


def test_every_remedy_the_rules_can_name_has_a_module():
    named = {decide(facts).action for facts in _every()} - {""}
    assert named <= set(registry.REMEDIES), f"no module for {sorted(named - set(registry.REMEDIES))}"
    assert set(ACTIONS) == set(registry.REMEDIES), "the rules' list of actions and the registry agree"
    try:
        registry.get("nonsense")
    except ValueError as err:
        assert "the remedies are" in str(err)


async def test_a_remedy_is_recorded_with_why_what_was_done_and_what_comes_next():
    from meta_harness.supervisor.loop import Supervisor

    with db.connect(autocommit=True) as conn:
        conn.execute("TRUNCATE supervisor.standing, supervisor.remedy")
    tasks, events = Tasks(), Events()
    now = datetime.now(UTC)
    failed = tasks.add(
        "failed",
        goal_id="g-remedy",
        status_reason="TaskFailed: every candidate failed (3 runs)",
        status_changed_at=(now - timedelta(minutes=10)).isoformat(),
    )

    await Supervisor(tasks, events, Workflows(), Host()).pass_once(now)

    [row] = ledger.read(task_id=failed["id"])
    assert (row["situation"], row["remedy"], row["try"], row["of"], row["outcome"]) == (
        "retry_due",
        "retry",
        1,
        3,
        "started",
    )
    assert row["because"] and row["did"] == "ran it again, after a wait" and "starts from the queue" in row["next"]
    assert ledger.read(goal_id="g-remedy")[0]["task_id"] == failed["id"]
    [(kind, payload, fields)] = [e for e in events.emitted if e[0] == "remedy_applied"]
    assert fields["task_id"] == failed["id"] and payload["outcome"] in OUTCOMES
    assert set(payload) == {"situation", "remedy", "try", "of", "outcome", "because", "did", "next"}


async def test_a_remedy_that_cannot_be_applied_is_recorded_as_failed_with_its_error():
    from meta_harness.supervisor.loop import Supervisor

    with db.connect(autocommit=True) as conn:
        conn.execute("TRUNCATE supervisor.standing, supervisor.remedy")
    tasks, events = Tasks(), Events()
    tasks.refuse = "the Task API is restarting"
    lost = tasks.add("running")
    supervisor = Supervisor(tasks, events, Workflows(), Host())
    await supervisor.pass_once(datetime.now(UTC))
    await supervisor.pass_once(datetime.now(UTC) + timedelta(minutes=5))

    [row] = ledger.read(task_id=lost["id"])
    assert (row["remedy"], row["outcome"]) == ("recover", "failed") and "restarting" in row["error"]
    assert row["next"] == "it is tried again later"


async def test_a_task_that_failed_on_its_own_runs_once_more_on_the_complex_tier_then_waits_for_an_operator():
    from meta_harness.supervisor.loop import Supervisor

    with db.connect(autocommit=True) as conn:
        conn.execute("TRUNCATE supervisor.standing, supervisor.remedy")
    tasks, events = Tasks(), Events()
    now = datetime.now(UTC)
    own = "TaskFailed: run 2 finished, but pi changed nothing in o/r"
    standard = tasks.add("failed", status_reason=own, status_changed_at=(now - timedelta(minutes=10)).isoformat())
    complex_ = tasks.add("failed", status_reason=own, status_changed_at=(now - timedelta(minutes=10)).isoformat())
    events.stored = [
        {"task_id": standard["id"], "event_type": "task_routed", "payload": {"tier": "standard"}},
        {"task_id": complex_["id"], "event_type": "task_routed", "payload": {"tier": "complex"}},
    ]
    supervisor = Supervisor(tasks, events, Workflows(), Host())

    await supervisor.pass_once(now)

    assert [(t, a) for t, a, *_ in tasks.driven] == [(standard["id"], "retry")]
    assert standard["complexity"] == "high", "its next route is the complex tier"
    [row] = ledger.read(task_id=standard["id"])
    assert (row["remedy"], row["try"], row["of"]) == ("retry_stronger", 1, 1)
    assert "stronger model" in row["did"] and "ask you" in row["next"]
    # The task already ran on the complex tier has no stronger model to try: an operator decides.
    assert ledger.read(task_id=complex_["id"]) == []

    # It failed again: its one stronger try is used, so now an operator is asked.
    standard.update(status="failed", status_changed_at=(now - timedelta(minutes=10)).isoformat())
    await supervisor.pass_once(now + timedelta(minutes=5))
    assert len(ledger.read(task_id=standard["id"])) == 1
