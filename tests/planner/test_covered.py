"""A split issue's own failed task ends when its parts did its work. No tracker, database or Task API is called."""

import asyncio

from meta_harness.planner import covered
from meta_harness.tasks import store as task_store
from meta_harness.tasks.client import TaskApiError


def _links(parts_state: str = "closed", **original) -> list[dict]:
    parts = ["jira:ABC-2", "jira:ABC-3"]
    whole = {"ref": "jira:ABC-1", "state": "open", "task_id": "t-1", "split_into": parts, "run_anyway": True}
    return [{**whole, **original}, *({"ref": p, "state": parts_state, "task_id": f"t-{p[-1]}"} for p in parts)]


class Tasks:
    def __init__(self, task: dict, refuses: bool = False) -> None:
        self.task, self.refuses, self.set = task, refuses, []

    def get(self, task_id: str) -> dict:
        return self.task

    def set_status(self, task_id: str, status: str, reason: str | None = None) -> dict:
        if self.refuses:
            raise TaskApiError(503, "the task service is restarting")
        self.set.append((task_id, status, reason))
        return self.task


def _end(links: list[dict], tasks: Tasks, monkeypatch) -> tuple[list[str], list[str], list[str]]:
    replaced: list[str] = []
    monkeypatch.setattr(covered, "_replaced", replaced.append)
    errors: list[str] = []
    return asyncio.run(covered.end_covered(links, tasks, errors)), errors, replaced


def test_a_failed_task_of_a_split_issue_whose_parts_all_merged_is_closed_and_the_issue_is_its_parts_again(monkeypatch):
    # The goal's page asked the operator to retry it, which would have written the parts' merged work again.
    tasks = Tasks({"id": "t-1", "status": "failed", "pr_url": None})

    ended, errors, replaced = _end(_links(), tasks, monkeypatch)

    assert (ended, errors, replaced) == (["t-1"], [], ["jira:ABC-1"])
    ((task_id, status, reason),) = tasks.set
    assert (task_id, status) == ("t-1", "closed")
    assert reason == "the issues its issue was split into are all done (ABC-2, ABC-3), so nothing is left for it to do"
    assert "closed" in task_store.TRANSITIONS["failed"], "a failed task may be closed as covered"


def test_a_task_is_left_alone_while_a_part_is_open_or_it_is_running_or_has_a_pull_request(monkeypatch):
    failed = {"id": "t-1", "status": "failed", "pr_url": None}
    for links, task in (
        (_links("open"), failed),
        (_links(), {**failed, "status": "running"}),
        (_links(), {**failed, "pr_url": "https://github.com/o/r/pull/9"}),
        (_links(run_anyway=False), failed),
        (_links(state="closed"), failed),
        (_links(task_id=None), failed),
    ):
        tasks = Tasks(task)
        assert _end(links, tasks, monkeypatch) == ([], [], []) and not tasks.set


def test_a_task_service_that_refuses_is_reported_and_the_issue_stays_as_it_was(monkeypatch):
    tasks = Tasks({"id": "t-1", "status": "failed", "pr_url": None}, refuses=True)

    ended, errors, replaced = _end(_links(), tasks, monkeypatch)

    assert ended == [] and replaced == [] and errors[0].startswith("jira:ABC-1: ")
