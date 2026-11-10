"""The scheduler starts a second RunTask workflow only for a task an operator sent back to the queue."""

import pytest

from meta_harness.run_service import start
from meta_harness.scheduler import service
from meta_harness.tasks import client


@pytest.mark.parametrize(("retries", "again"), [(0, False), (None, False), (2, True)])
async def test_a_task_sent_back_may_start_another_workflow(monkeypatch, retries, again):
    started = []

    class Tasks:
        def __init__(self, api_key=None):
            pass

        def get(self, task_id):
            return {"id": task_id, "retries": retries}

    async def fake_start(task_id, again=False):
        started.append((task_id, again))
        return task_id

    monkeypatch.setattr(client, "TaskClient", Tasks)
    monkeypatch.setattr(start, "start", fake_start)

    await service.start_run_task("t-1")

    assert started == [("t-1", again)]
