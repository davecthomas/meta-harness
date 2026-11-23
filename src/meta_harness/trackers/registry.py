"""The trackers the meta-harness supports, and this deployment's choice among them.

Supported is code: one entry per adapter, with the settings it takes, for the console to draw its form and
for a saved configuration to be checked against. Which are set up, and which one a new goal uses, is the
`trackers` configuration (config/trackers.json, or its active version in the console):

    {"default": "jira", "trackers": {"github": {}, "jira": {"site": ..., "projects": [...]}}}

It may also name the label every issue the meta-harness creates or changes carries, in any tracker
(`"label": "ai:meta-harness"`, which is what it is when the configuration names none).

A goal uses one tracker, the default when it is created unless it names another, and keeps it.
"""

import json
from typing import Any

from meta_harness.run_service.paths import REPO_ROOT
from meta_harness.trackers import github, jira, own
import re

from meta_harness.trackers.base import MARK_LABEL, Supported

# Every tracker the meta-harness supports. To add one: write its adapter module, with a SPEC, and list it here.
SUPPORTED: dict[str, Supported] = {module.SPEC.scheme: module.SPEC for module in (github, jira)}
# The meta-harness's own tracker, for tasks kept in no outside system (trackers/own.py). It is always there and
# has nothing to set up, so it is not part of the configuration: a goal that uses no tracker uses it.
OWN = own.SPEC


def spec(scheme: str) -> Supported | None:
    """A tracker's description by its scheme: a supported one's, or the meta-harness's own."""
    return OWN if scheme == OWN.scheme else SUPPORTED.get(scheme)


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
    """The tracker a new goal uses when it names none: the configuration's default, and the meta-harness's
    own, which is no outside tracker at all, when none is set up."""
    configured = document()
    if not (configured.get("trackers") or {}):
        return OWN.scheme
    return str(configured.get("default") or "github")


def mark_label() -> str:
    """The label every issue the meta-harness creates or changes carries."""
    return str(document().get("label") or MARK_LABEL)


def settings(scheme: str) -> dict[str, Any] | None:
    """A tracker's settings, or None for one that is not set up. The meta-harness's own always is."""
    if scheme == OWN.scheme:
        return {}
    found = (document().get("trackers") or {}).get(scheme)
    return dict(found) if isinstance(found, dict) else None


def check(document: dict[str, Any], others: dict[str, dict[str, Any]] | None) -> list[str]:
    """Why a trackers configuration cannot be used; empty when it can."""
    trackers = document.get("trackers")
    if not isinstance(trackers, dict):
        return ["trackers must be an object of the trackers set up, by name; empty when goals use none"]
    reasons = []
    for scheme, spec in trackers.items():
        if scheme not in SUPPORTED:
            reasons.append(f"{scheme}: not a supported tracker; supported: {', '.join(sorted(SUPPORTED))}")
        elif not isinstance(spec, dict):
            reasons.append(f"{scheme}: a tracker's settings are an object")
    chosen = document.get("default")
    # With no tracker set up there is none to default to: every goal keeps its tasks in the meta-harness.
    if trackers and chosen not in trackers and chosen != OWN.scheme:
        reasons.append(
            f"default must be one of the trackers set up ({', '.join(sorted(trackers))}), or {OWN.scheme} for "
            f"goals that use no tracker, not {chosen!r}"
        )
    if not trackers and chosen not in (None, "", OWN.scheme):
        reasons.append(f"default names {chosen!r}, which is not set up: set it up, or leave default out")
    label = document.get("label", MARK_LABEL)
    if not isinstance(label, str) or not label or len(label) > 50 or re.search(r"[\s,]", label):
        reasons.append(
            "label is the label put on every issue Octofusion creates or changes. It is up to 50 "
            f"characters with no space or comma, such as {MARK_LABEL} (a Jira label has no spaces)"
        )
    registered = (others or {}).get("repos", {}).get("repos")
    names = {name.lower() for name in registered} if isinstance(registered, dict) else None
    for scheme, spec in trackers.items():
        if scheme in SUPPORTED and isinstance(spec, dict):
            reasons += [f"{scheme}: {reason}" for reason in SUPPORTED[scheme].check(spec, names)]
    return reasons
