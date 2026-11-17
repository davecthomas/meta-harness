"""The trackers the meta-harness supports, and this deployment's choice among them.

Supported is code: one entry per adapter, with the settings it takes, for the console to draw its form and
for a saved configuration to be checked against. Which are set up, and which one a new goal uses, is the
`trackers` configuration (config/trackers.json, or its active version in the console):

    {"default": "jira", "trackers": {"github": {}, "jira": {"site": ..., "projects": [...]}}}

A goal uses one tracker, the default when it is created unless it names another, and keeps it.
"""

import json
from typing import Any

from meta_harness.run_service.paths import REPO_ROOT
from meta_harness.trackers import github, jira
from meta_harness.trackers.base import Supported

# Every tracker the meta-harness supports. To add one: write its adapter module, with a SPEC, and list it here.
SUPPORTED: dict[str, Supported] = {module.SPEC.scheme: module.SPEC for module in (github, jira)}


def seed() -> dict[str, Any]:
    """The deployment's file: config/trackers.local.json when there is one (git ignores it), else the shipped
    config/trackers.json, which sets up GitHub alone."""
    local = REPO_ROOT / "config" / "trackers.local.json"
    path = local if local.is_file() else REPO_ROOT / "config" / "trackers.json"
    return json.loads(path.read_text(encoding="utf-8"))


def document() -> dict[str, Any]:
    """The configuration in use: the console's active version, or the deployment's file."""
    from meta_harness.config import store

    return store.current_document("trackers")


def default() -> str:
    """The tracker a new goal uses when it names none."""
    return str(document().get("default") or "github")


def settings(scheme: str) -> dict[str, Any] | None:
    """A tracker's settings, or None for one that is not set up."""
    found = (document().get("trackers") or {}).get(scheme)
    return dict(found) if isinstance(found, dict) else None


def check(document: dict[str, Any], others: dict[str, dict[str, Any]] | None) -> list[str]:
    """Why a trackers configuration cannot be used; empty when it can."""
    trackers = document.get("trackers")
    if not isinstance(trackers, dict) or not trackers:
        return ["trackers must be an object of the trackers set up, by name, with at least one"]
    reasons = []
    for scheme, spec in trackers.items():
        if scheme not in SUPPORTED:
            reasons.append(f"{scheme}: not a supported tracker; supported: {', '.join(sorted(SUPPORTED))}")
        elif not isinstance(spec, dict):
            reasons.append(f"{scheme}: a tracker's settings are an object")
    chosen = document.get("default")
    if chosen not in trackers:
        reasons.append(f"default must be one of the trackers set up ({', '.join(sorted(trackers))}), not {chosen!r}")
    registered = (others or {}).get("repos", {}).get("repos")
    names = {name.lower() for name in registered} if isinstance(registered, dict) else None
    for scheme, spec in trackers.items():
        if scheme in SUPPORTED and isinstance(spec, dict):
            reasons += [f"{scheme}: {reason}" for reason in SUPPORTED[scheme].check(spec, names)]
    return reasons
