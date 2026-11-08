"""The repo registry: the repos the meta-harness may change, each with its tracker and default checks.

The registry is deployment data, not code: config/repos.json ships empty.
A deployment supplies its own as MH_REPOS_CONFIG, or locally as
config/repos.local.json (git ignores it; config/repos.example.json shows the
shape). With MH_CONFIG_SOURCE=db the registry is the active `repos` version
an operator edits in the console, seeded from that file.
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


def registry() -> dict[str, Repo]:
    from meta_harness.config import store

    if store.from_database():
        return _parse(store.active_document("repos")[1])
    return _file_registry()


@cache
def _file_registry() -> dict[str, Repo]:
    return _parse(json.loads(registry_path().read_text(encoding="utf-8")))


def _parse(loaded: dict) -> dict[str, Repo]:
    return {
        name.lower(): Repo(name, spec.get("tracker", "github"), list(spec.get("default_checks", [])))
        for name, spec in loaded["repos"].items()
    }


def get(name: str) -> Repo | None:
    return registry().get(name.lower())
