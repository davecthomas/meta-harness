"""Muxxy's dollar-hours per successful task, placed among measured harness and model pairs (tasks/leaderboard.py)."""

import json

from meta_harness.tasks import leaderboard


def _file(tmp_path, rows):
    path = tmp_path / "ranking.json"
    ranking = [{"harness": h, "model": m, "dollar_hours_per_successful_task": v} for h, m, v in rows]
    path.write_text(json.dumps({"metric": "dollar-hours per successful task", "ranking": ranking}))
    return path


ROWS = [("cursor", "fast", 0.16), ("pi", "opus", 1.88), ("claude", "opus", 596.82)]


def test_muxxy_is_placed_among_the_measured_pairs_by_its_figure(tmp_path):
    found = leaderboard.board(2.5, tasks=14, path=_file(tmp_path, ROWS))

    assert [(r["rank"], r["harness"], r["muxxy"]) for r in found["rows"]] == [
        (1, "cursor", False),
        (2, "pi", False),
        (3, "Muxxy", True),
        (4, "claude", False),
    ]
    assert (found["rank"], found["of"], found["tasks"]) == (3, 4, 14)
    assert found["multiple_of_best"] == 15.62, "how many times the cheapest pair's figure"


def test_a_tie_goes_to_muxxy_and_the_cheapest_is_one_times_the_best(tmp_path):
    path = _file(tmp_path, ROWS)
    assert leaderboard.board(1.88, path=path)["rank"] == 2
    first = leaderboard.board(0.05, path=path)
    assert (first["rank"], first["multiple_of_best"]) == (1, 1.0)


def test_with_no_figure_the_board_is_the_measured_pairs_alone(tmp_path):
    found = leaderboard.board(None, path=_file(tmp_path, ROWS))
    assert found["rank"] is None and found["of"] == 3 and not any(r["muxxy"] for r in found["rows"])
    assert leaderboard.board(2.0, path=tmp_path / "missing.json")["rows"] == []


def test_the_shipped_file_holds_a_harness_a_model_and_the_one_figure_and_nothing_else():
    data = json.loads(leaderboard.FILE.read_text(encoding="utf-8"))
    assert set(data) == {"metric", "direction", "ranking"}
    assert data["ranking"], "there are pairs to rank against"
    for row in data["ranking"]:
        assert set(row) == {"harness", "model", "dollar_hours_per_successful_task"}
    figures = [row["dollar_hours_per_successful_task"] for row in data["ranking"]]
    assert figures == sorted(figures), "cheapest first"
