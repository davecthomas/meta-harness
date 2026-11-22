# Created by the meta-harness in run t-45c4a1fc56e9:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""Fix with AI with a fake HTTP Gateway, a fake tracker and a fake Task API (planner/fix_issue.py,
planner/issue_fix.py): a draft writes nothing; accept adds the section, labels and sizes again; an issue whose
task is running is left as it is and its draft is kept for the next run; a model failure changes nothing.

No model, no GitHub.
"""

from meta_harness.planner import issue_fix

from .test_fix_issue import gateway, world  # noqa: F401
from .test_planner import OPERATOR, _number, migrated, registry  # noqa: F401
from .test_sizing import REPO, _goal, _payloads, _sweep, _task


def _url(goal: dict, route: str) -> str:
    return f"/v1/goals/{goal['id']}/issues/{route}"


def _running(client, tracker, tasks) -> tuple[dict, str, dict]:
    """A goal holding an issue that lacks acceptance checks, whose task is running."""
    goal = _goal(client)
    ref = tracker.add(REPO, _number(), body="Fix the sizing card.\n\nPaths: console/src", labels=[goal["label"]])
    _sweep(client, goal)
    task = _task(tasks, ref)
    tasks.set_status(task["id"], "running")
    return goal, ref, task


def test_a_draft_writes_nothing_to_the_tracker(world, gateway):  # noqa: F811
    client, events, tracker, tasks, sizer = world
    goal = _goal(client, strict_issues=True)
    ref = tracker.add(REPO, _number(), body="Fix the sizing card.\n\nPaths: console/src", labels=[goal["label"]])
    _sweep(client, goal)
    before = tracker.issues[ref]

    drafted = client.post(_url(goal, "fix-draft"), json={"ref": ref}, headers=OPERATOR)

    assert drafted.status_code == 200, drafted.text
    assert drafted.json()["kept"] is False and [d["part"] for d in drafted.json()["drafts"]] == ["acceptance_checks"]
    assert len(gateway["seen"]) == 1
    assert tracker.issues[ref] == before and tracker.sections == [] and tracker.marked == []
    assert issue_fix.kept(goal["id"], ref) is None


def test_accept_adds_the_section_labels_the_issue_and_sizes_it_again(world):  # noqa: F811
    client, events, tracker, tasks, sizer = world
    goal = _goal(client, strict_issues=True)
    ref = tracker.add(REPO, _number(), body="Fix the sizing card.\n\nPaths: console/src", labels=[goal["label"]])
    _sweep(client, goal)
    sized_before = len(_payloads(events, "issue_sized"))

    accepted = client.post(
        _url(goal, "fix-accept"),
        json={"ref": ref, "parts": [{"part": "acceptance_checks", "text": "uv run pytest -q tests"}]},
        headers=OPERATOR,
    )

    assert accepted.status_code == 200, accepted.text
    assert [(r, h) for r, h, _ in tracker.sections] == [(ref, "Acceptance checks")]
    assert tracker.marked == [ref]
    assert tracker.issues[ref].checks == ["uv run pytest -q tests"]
    assert sizer.calls.count(ref) == 2 and len(_payloads(events, "issue_sized")) == sized_before + 1
    assert accepted.json()["sizing"]["missing"] == []


def test_a_running_tasks_issue_is_left_as_it_is_and_its_draft_is_kept_for_the_next_run(world, gateway):  # noqa: F811
    client, events, tracker, tasks, sizer = world
    goal, ref, task = _running(client, tracker, tasks)
    before = tracker.issues[ref]
    edited = {"part": "acceptance_checks", "text": "- `make test`"}

    accepted = client.post(_url(goal, "fix-accept"), json={"ref": ref, "parts": [edited]}, headers=OPERATOR)

    assert accepted.status_code == 409, accepted.text
    detail = accepted.json()["detail"]
    assert "is running" in detail and "left as it is" in detail and "The draft is kept" in detail
    assert tracker.issues[ref] == before and tracker.sections == [] and tracker.marked == []
    kept = issue_fix.kept(goal["id"], ref)
    assert kept["parts"] == [edited] and kept["by"] == "dave"

    # While the task runs, a draft is refused without a model call, and says the kept draft waits.
    drafted = client.post(_url(goal, "fix-draft"), json={"ref": ref}, headers=OPERATOR)
    assert drafted.status_code == 409 and "kept draft is offered once the task ends" in drafted.json()["detail"]
    assert gateway["seen"] == []

    # Once the task ends, the kept draft is offered again with no model call.
    tasks.set_status(task["id"], "failed")
    offered = client.post(_url(goal, "fix-draft"), json={"ref": ref}, headers=OPERATOR)
    assert offered.status_code == 200, offered.text
    assert offered.json()["kept"] is True
    assert [(d["part"], d["heading"], d["text"]) for d in offered.json()["drafts"]] == [
        ("acceptance_checks", "Acceptance checks", "- `make test`")
    ]
    assert gateway["seen"] == [] and _payloads(events, "issue_fix_drafted") == []
    # fresh asks the model past the kept draft.
    fresh = client.post(_url(goal, "fix-draft"), json={"ref": ref, "fresh": True}, headers=OPERATOR)
    assert fresh.status_code == 200 and fresh.json()["kept"] is False and len(gateway["seen"]) == 1

    # Accepting it writes the issue and forgets the kept draft.
    taken = client.post(_url(goal, "fix-accept"), json={"ref": ref, "parts": kept["parts"]}, headers=OPERATOR)
    assert taken.status_code == 200, taken.text
    assert [h for _, h, _ in tracker.sections] == ["Acceptance checks"] and tracker.marked == [ref]
    assert issue_fix.kept(goal["id"], ref) is None


def test_a_model_failure_after_its_retries_changes_nothing(world, gateway):  # noqa: F811
    client, events, tracker, tasks, sizer = world
    goal = _goal(client, strict_issues=True)
    ref = tracker.add(REPO, _number(), body="Fix the sizing card.\n\nPaths: console/src", labels=[goal["label"]])
    _sweep(client, goal)
    before = tracker.issues[ref]
    sized_before = len(_payloads(events, "issue_sized"))
    gateway["status"] = 503

    drafted = client.post(_url(goal, "fix-draft"), json={"ref": ref}, headers=OPERATOR)

    assert drafted.status_code == 502 and "Try Fix with AI again" in drafted.json()["detail"]
    assert len(gateway["seen"]) > 1, "the gateway call is retried before the failure is reported"
    assert tracker.issues[ref] == before and tracker.sections == [] and tracker.marked == []
    assert _payloads(events, "issue_fix_drafted") == [] and len(_payloads(events, "issue_sized")) == sized_before
    assert issue_fix.kept(goal["id"], ref) is None
