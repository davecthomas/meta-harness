"""The repo registry: the repos the meta-harness may change, each with its tracker and default checks.

The registry is deployment data, not code: config/repos.json ships empty.
A deployment supplies its own as MH_REPOS_CONFIG, or locally as
config/repos.local.json (git ignores it; config/repos.example.json shows the
shape). Configuration edited in the operator console replaces the file later.
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


def registry_path() -> Path:
    configured = os.environ.get("MH_REPOS_CONFIG")
    if configured:
        return Path(configured)
    local = REPO_ROOT / "config" / "repos.local.json"
    return local if local.is_file() else REPO_ROOT / "config" / "repos.json"


@cache
def registry() -> dict[str, Repo]:
    loaded = json.loads(registry_path().read_text(encoding="utf-8"))
    return {
        name.lower(): Repo(name, spec.get("tracker", "github"), list(spec.get("default_checks", [])))
        for name, spec in loaded["repos"].items()
    }


def get(name: str) -> Repo | None:
    return registry().get(name.lower())
