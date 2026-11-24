"""The Performance page's numbers: cost per task, pull request and goal, and dollar-hours per completed task."""

from datetime import datetime, timedelta, timezone

from meta_harness.tasks import performance

NOW = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)


def _events(*specs: tuple) -> list[dict]:
    """Events from (task, run, type, payload, hours ago), numbered in order as the event service would."""
    return [
        {
            "task_id": task,
            "run_id": run,
            "event_type": kind,
            "payload": payload,
            "position": n,
            "time_utc": (NOW - timedelta(hours=ago)).isoformat(),
        }
        for n, (task, run, kind, payload, ago) in enumerate(specs, 1)
    ]


def _run(task: str, run: str, tier: str, usd: str, began: float, ended: float) -> list[tuple]:
    return [
        (task, run, "run_prompt", {"run_id": run, "kind": "task"}, began),
        (task, run, "attempt_finished", {"tier": tier, "spent_usd": usd}, ended),
    ]


def test_the_numbers_are_worked_out_from_each_tasks_runs_checks_and_merge():
    recorded = _events(
        # t-1: a $4 task run of 2 hours and a $2 review run of 1 hour; checks passed and it merged. C = 1.
        *_run("t-1", "r-1", "standard", "4.00", 10, 8),
        ("t-1", None, "pull_request_opened", {"pr_url": "u", "branch": "b"}, 8),
        *_run("t-1", "r-2", "review", "2.00", 8, 7),
        ("t-1", None, "pull_request_checks", {"head_sha": "a", "state": "passed"}, 7),
        ("t-1", None, "pull_request_merged", {"pr_url": "u", "head_sha": "a"}, 7),
        # t-2: a $10 run of 1 hour; its checks failed and it never merged. C = 0, and its spend is wasted.
        *_run("t-2", "r-3", "standard", "10.00", 6, 5),
        ("t-2", None, "pull_request_opened", {"pr_url": "v", "branch": "c"}, 5),
        ("t-2", None, "pull_request_checks", {"head_sha": "b", "state": "failed", "failed": ["tests"]}, 5),
        # t-3: a $1 run with no pull request, and of no goal.
        *_run("t-3", "r-4", "standard", "1.00", 4, 3.5),
        # t-4: finished long ago, outside the range.
        *_run("t-4", "r-5", "standard", "50.00", 900, 899),
        # A deploy's smoke task is no work: left out of everything.
        *_run("t-5", "r-6", "standard", "0.01", 2, 1.9),
        # g-1's planning task: part of the goal's cost and the total, and not one of the tasks.
        *_run("t-6", "r-7", "standard", "2.00", 12, 11.5),
    )
    goals = {"t-1": "g-1", "t-2": "g-1", "t-3": None, "t-4": "g-2", "t-5": None, "t-6": "g-1"}
    types = {"t-5": "smoke", "t-6": "plan"}

    found = performance.summary(recorded, goals, NOW - timedelta(days=7), NOW, types)

    assert (found["tasks"], found["merged"]) == (3, 1)
    assert found["cost_per_task"] == {"average": 5.6667, "median": 6.0, "count": 3}
    assert found["cost_per_pull_request"] == {"average": 8.0, "median": 8.0, "count": 2}
    assert found["cost_per_goal"] == {"average": 18.0, "median": 18.0, "count": 1}
    # Σ(D × H) = 4×2 + 2×1 + 10×1 + 1×0.5 = 20.5, over Σ C = 1: failed work counts against the number.
    assert found["dollar_hours_per_completed_task"] == {"average": 20.5, "median": 10.0, "count": 1}
    assert (found["total_usd"], found["wasted_usd"], found["review_loop_usd"]) == ("19.00", "11.00", "2.00")
    assert found["planning_usd"] == "2.00"
    assert found["hours"] == 4.5


def test_a_range_with_no_finished_task_has_no_numbers_to_show():
    found = performance.summary([], {}, NOW - timedelta(days=7), NOW)

    assert found["tasks"] == 0 and found["cost_per_task"] == {"average": None, "median": None, "count": 0}
    assert found["dollar_hours_per_completed_task"]["average"] is None
