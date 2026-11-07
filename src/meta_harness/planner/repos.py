"""The repo registry: the repos the meta-harness may change, each with its tracker and default checks.

Read from config/repos.json, or MH_REPOS_CONFIG. Configuration edited in the
operator console replaces this file later; the shape stays the same.
"""

import json
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from meta_harness.run_service.paths import REPO_ROOT


@dataclass(frozen=True)
class Repo:
    name: str
    tracker: str
    default_checks: list[str]


@cache
def registry() -> dict[str, Repo]:
    path = Path(os.environ.get("MH_REPOS_CONFIG") or REPO_ROOT / "config" / "repos.json")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return {
        name.lower(): Repo(name, spec.get("tracker", "github"), list(spec.get("default_checks", [])))
        for name, spec in loaded["repos"].items()
    }


def get(name: str) -> Repo | None:
    return registry().get(name.lower())
