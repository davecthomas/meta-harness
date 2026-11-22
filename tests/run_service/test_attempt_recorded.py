"""A run's spend is stored before it is counted as spent, and a slow start keeps its activity alive."""

import asyncio

import pytest
from temporalio.testing import ActivityEnvironment

from meta_harness.run_service import activities, task_activities
from meta_harness.run_service.shared import Attempt, AttemptRecord


def _record() -> AttemptRecord:
    attempt = Attempt(
        attempt=1, tier="standard", candidate="c", run_id="t-1:run-1", decision="finished", detail="", spent_usd="1.25"
    )
    return AttemptRecord(task_id="t-1", attempt=attempt, candidate=None)  # type: ignore[arg-type]


@pytest.fixture
def world(monkeypatch):
    done: list[str] = []
    stored: dict[str, dict] = {}
    failing = [True]

    def append(event_type, payload, **fields):
        if failing[0]:
            raise RuntimeError("the event service is down")
        stored[fields["event_id"]] = {"type": event_type, **payload, "task_id": fields["task_id"]}
        done.append("event")

    monkeypatch.setattr(task_activities, "_insert_run", lambda record: done.append("run"))
    monkeypatch.setattr(task_activities.goal_grants, "release", lambda task_id: done.append("release"))
    monkeypatch.setattr(task_activities.events, "append", append)
    return done, stored, failing


async def test_a_spend_event_that_is_not_stored_fails_the_activity_and_keeps_the_grant(world):
    """A goal's spend is summed from attempt_finished. Queued in memory, one was lost with its worker, after the
    grant that held the money back was already released: spend that no limit counted."""
    done, stored, failing = world

    with pytest.raises(RuntimeError, match="event service is down"):
        await ActivityEnvironment().run(task_activities.record_attempt, _record())
    assert "release" not in done and stored == {}

    # Temporal runs the activity again; the same run stores one event, and only then is the grant released.
    failing[0] = False
    await ActivityEnvironment().run(task_activities.record_attempt, _record())
    await ActivityEnvironment().run(task_activities.record_attempt, _record())

    assert len(stored) == 1 and next(iter(stored.values()))["spent_usd"] == "1.25"
    assert done[-2:] == ["event", "release"]


async def test_a_slow_start_heartbeats_until_it_is_done(monkeypatch):
    """The clone and the sandbox start can outlast the activity's heartbeat timeout. Without a heartbeat the
    run was taken for dead and started again over the first."""
    monkeypatch.setattr(activities, "SETUP_HEARTBEAT_SECONDS", 0.01)
    beats: list = []
    env = ActivityEnvironment()
    env.on_heartbeat = lambda *details: beats.append(details)

    async def slow() -> str:
        await asyncio.sleep(0.08)
        return "cloned"

    async def run() -> str:
        return await activities._beating({"last_event_id": 0}, slow())

    assert await env.run(run) == "cloned"
    assert len(beats) >= 3, "it said it was alive while the work went on"
    count = len(beats)
    await asyncio.sleep(0.05)
    assert len(beats) == count, "and stopped when the work was done"
