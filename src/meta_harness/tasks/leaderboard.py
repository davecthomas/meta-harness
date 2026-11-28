"""Where Muxxy's dollar-hours per successful task stands among measured harness and model pairs.

The figures it is ranked against are in config/dollar_hours_ranking.json: a harness, a model and its
dollar-hours per successful task, by the formula the Performance page uses (tasks/performance.py), and nothing
else. Lower is better. A harness and model measured more than one way is in the file once for each.

Muxxy's own figure is over whatever tasks finished in the days in view, which are not the tasks those pairs
were measured on: the answer is a place among them, not a like-for-like result, and the page says so.
"""

import json
from functools import cache
from pathlib import Path
from typing import Any

from meta_harness.run_service.paths import REPO_ROOT

FILE = REPO_ROOT / "config" / "dollar_hours_ranking.json"
MUXXY = "Muxxy"


@cache
def measured(path: Path = FILE) -> dict[str, Any]:
    """The measured pairs, cheapest first, as the file has them. An empty board when there is no file."""
    if not path.is_file():
        return {"ranking": []}
    return json.loads(path.read_text(encoding="utf-8"))


def board(value: float | None, tasks: int = 0, path: Path = FILE) -> dict[str, Any]:
    """The measured pairs with Muxxy placed among them by its figure, cheapest first, each with its rank.
    value: Muxxy's dollar-hours per completed task for the tasks in view, None when it has none: the board is
    then the measured pairs alone, and `rank` is None. tasks: how many tasks that figure is over."""
    data = measured(path)
    rows = [
        {
            "harness": row["harness"],
            "model": row["model"],
            "value": float(row["dollar_hours_per_successful_task"]),
            "muxxy": False,
        }
        for row in data.get("ranking") or []
    ]
    if value is not None and rows:
        rows.append({"harness": MUXXY, "model": "", "value": float(value), "muxxy": True})
    rows.sort(key=lambda row: (row["value"], not row["muxxy"]))
    for place, row in enumerate(rows, 1):
        row["rank"] = place
    mine = next((row for row in rows if row["muxxy"]), None)
    best = rows[0]["value"] if rows else None
    return {
        "metric": data.get("metric") or "dollar-hours per successful task",
        "rank": mine["rank"] if mine else None,
        "of": len(rows),
        # How many times the cheapest pair's figure Muxxy's is: 1 when it is the cheapest.
        "multiple_of_best": round(mine["value"] / best, 2) if mine and best else None,
        # How many tasks Muxxy's figure is over.
        "tasks": tasks,
        "rows": rows,
    }
