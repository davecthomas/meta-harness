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
