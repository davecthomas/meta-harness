"""Where a task's money went: its runs, with what each cost and did, from the events they recorded."""

from meta_harness.tasks import spend


def _attempt(position: int, run: str, tier: str, usd: str, status: str = "FINISHED", end: str = "completed") -> dict:
    payload = {
        "attempt": position,
        "tier": tier,
        "candidate": "harness:a/b:c",
        "spent_usd": usd,
        "status": status,
        "end_reason": end,
    }
    return {"position": position * 10, "event_type": "attempt_finished", "run_id": run, "payload": payload}


def _report(position: int, run: str, turns: int, tokens_in: int, cached: int, out: int) -> dict:
    usage = {"calls": turns + 1, "input_tokens": tokens_in, "cached_input_tokens": cached, "output_tokens": out}
    return {
        "position": position * 10 - 1,
        "event_type": "run_finished",
        "run_id": run,
        "payload": {"turns": turns, "usage": usage},
    }


EVENTS = [
    _report(1, "t:run-1", 70, 8_137_130, 7_000_000, 30_000),
    _attempt(1, "t:run-1", "complex", "4.0044"),
    _report(2, "t:run-2", 13, 550_224, 500_000, 2_000),
    _attempt(2, "t:run-2", "review", "0.2425"),
    # A run its sandbox never started reports no turns.
    _attempt(3, "t:run-3", "fix", "0.8500", status="ERROR", end="spend_limit"),
]


def test_each_run_is_a_row_with_its_kind_what_it_did_and_what_it_cost():
    first, review, fix = spend.runs(EVENTS)
    assert (first["kind"], first["tier"], first["turns"], first["model_calls"]) == ("task", "complex", 70, 71)
    assert (first["input_tokens"], first["cached_input_tokens"], first["output_tokens"]) == (
        8_137_130,
        7_000_000,
        30_000,
    )
    assert first["usd"] == "4.0044" and first["run_id"] == "t:run-1"
    assert review["kind"] == "review" and review["usd"] == "0.2425"
    assert (fix["kind"], fix["status"], fix["end_reason"], fix["turns"]) == ("fix", "ERROR", "spend_limit", None)


def test_the_totals_add_the_runs_up_in_all_and_by_kind():
    totals = spend.totals(spend.runs(EVENTS))
    assert totals["usd"] == "5.0969" and totals["runs"] == 3 and totals["turns"] == 83
    assert totals["by_kind"] == {"task": "4.0044", "review": "0.2425", "fix": "0.8500"}
    assert totals["input_tokens"] == 8_687_354 and totals["cached_input_tokens"] == 7_500_000


def test_a_task_with_no_run_yet_spent_nothing():
    assert spend.runs([]) == []
    assert spend.totals([])["usd"] == "0" and spend.totals([])["by_kind"]["review"] == "0"
