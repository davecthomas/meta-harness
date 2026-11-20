"""Remedies: every one the rules can name has a module, and every try is recorded in one fixed shape."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

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


def test_a_spend_limit_is_raised_only_for_the_task_within_twice_it_and_the_goals_room():
    from meta_harness import budget_request
    from meta_harness.supervisor.remedies.raise_limit import raise_to

    def held(left, limit, need, why=None):
        reason = budget_request.hold_reason("Its next run", need, why or budget_request.task_why(left, limit))
        return {"status_reason": reason}

    assert raise_to(held("0.40", "8", "3"), None) == Decimal("11")
    assert raise_to(held("0.40", "8", "12"), None) is None, "past twice the limit, an operator decides"
    assert raise_to(held("0.40", "8", "3"), {"max_usd": "40", "spent_usd": "38"}) is None, "the goal has no room"
    assert raise_to(held("0.40", "8", "3"), {"max_usd": "40", "spent_usd": "20"}) == Decimal("11")
    goal_hold = held("0", "0", "3", why=budget_request.goal_why("g-1", "40", "40", "0", 2))
    assert raise_to(goal_hold, None) is None, "a goal's budget is never raised by a remedy"


async def test_a_task_held_at_its_own_limit_is_raised_once_and_then_asks():
    from meta_harness import budget_request
    from meta_harness.supervisor.loop import Supervisor

    with db.connect(autocommit=True) as conn:
        conn.execute("TRUNCATE supervisor.standing, supervisor.remedy")
    tasks, events, workflows = Tasks(), Events(), Workflows()
    reason = budget_request.hold_reason("Its next run", "3", budget_request.task_why("0.40", "8"))
    task = tasks.add("running", waiting_on="budget", status_reason=reason)
    workflows.states[task["id"]] = "running"
    supervisor = Supervisor(tasks, events, workflows, Host())

    await supervisor.pass_once(datetime.now(UTC))

    assert tasks.budgeted == [(task["id"], "raise", "11.00")]
    [row] = ledger.read(task_id=task["id"])
    assert row["did"] == "raised its spend limit from $8.00 to $11.00, enough for its next run"
    task.update(waiting_on="budget", status_reason=reason)
    await supervisor.pass_once(datetime.now(UTC) + timedelta(minutes=5))
    assert len(tasks.budgeted) == 1, "its one raise is used: now an operator decides"


async def test_a_failure_the_rules_cannot_read_is_read_once_by_a_model_and_the_rules_act_on_it():
    from meta_harness.supervisor.loop import Supervisor

    with db.connect(autocommit=True) as conn:
        conn.execute("TRUNCATE supervisor.standing, supervisor.remedy")
    tasks, events = Tasks(), Events()
    now = datetime.now(UTC)
    odd = "TaskFailed: the sandbox's disk filled while it unpacked node_modules"
    task = tasks.add("failed", status_reason=odd, status_changed_at=(now - timedelta(minutes=10)).isoformat())
    asked = []

    async def diagnoser(reason):
        asked.append(reason)
        return "transient", "a full disk is the machine's, not the task's", "claude:test-model", "0.0002"

    supervisor = Supervisor(tasks, events, Workflows(), Host(), diagnoser=diagnoser)
    await supervisor.pass_once(now)

    assert asked == [odd]
    assert [(t, a) for t, a, *_ in tasks.driven] == [(task["id"], "retry")], "read as the system's: retried"
    diagnosis = next(r for r in ledger.read(task_id=task["id"]) if r["remedy"] == "diagnose")
    assert diagnosis["finding"] == "transient" and diagnosis["did"].startswith("read its failure as the system")
    assert any(e[0] == "failure_diagnosed" and e[1]["usd_cost"] == "0.0002" for e in events.emitted)

    # The same failure again is not read again: the ledger has the answer.
    task.update(status="failed", status_reason=odd, status_changed_at=(now - timedelta(minutes=10)).isoformat())
    await supervisor.pass_once(now + timedelta(minutes=5))
    assert asked == [odd]


async def test_a_failure_that_cannot_be_read_is_left_to_an_operator_as_before():
    from meta_harness.supervisor import diagnose
    from meta_harness.supervisor.loop import Supervisor

    with db.connect(autocommit=True) as conn:
        conn.execute("TRUNCATE supervisor.standing, supervisor.remedy")
    tasks, events = Tasks(), Events()
    task = tasks.add(
        "failed", status_reason="TaskFailed: something nobody has seen", status_changed_at=datetime.now(UTC).isoformat()
    )
    events.stored = [{"task_id": task["id"], "event_type": "task_routed", "payload": {"tier": "complex"}}]

    async def broken(reason):
        raise diagnose.DiagnoseError("the HTTP Gateway answered 503: busy")

    await Supervisor(tasks, events, Workflows(), Host(), diagnoser=broken).pass_once(datetime.now(UTC))
    assert tasks.driven == [] and not [r for r in ledger.read(task_id=task["id"]) if r["remedy"] == "diagnose"]
