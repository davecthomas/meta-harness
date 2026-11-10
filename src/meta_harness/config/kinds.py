"""The configuration kinds: where each one's first version comes from, and how a new version is checked.

- `tiers`: the ai-api-unified router's `router` section, as in models.json. Its
  tier candidates are harness pairs, `harness:<profile>/<engine>:<model>`.
- `profiles`: the harness profiles, as in config/harness_profiles.json.
- `repos`: the repo registry, as in config/repos.example.json.

A kind's first version is seeded from the deployment's file. A new version is
refused, with every reason, when the service that reads it would refuse it at
startup: a malformed router, a candidate naming a profile that does not exist,
a profile with no tools, a repo with no tracker.
"""

import json
import os
from dataclasses import dataclass
from typing import Any, Callable

from ai_api_unified.routing.router_config import load_router_config

from meta_harness.run_service.routing import replaced_engine_reason

from meta_harness.run_service.paths import REPO_ROOT

KNOWN_TOOLS = {"read", "write", "edit", "ls", "find", "grep", "bash", "submit_review", "update_plan"}
THINKING_LEVELS = {"off", "minimal", "low", "medium", "high", "max"}
TRACKERS = {"github"}


class ConfigError(ValueError):
    """A document the owning service would refuse. `reasons` lists every problem found."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass(frozen=True)
class Kind:
    name: str
    title: str
    description: str
    seed: Callable[[], dict[str, Any]]
    # Problems with a document, given the other kinds' active documents.
    check: Callable[[dict[str, Any], dict[str, dict[str, Any]]], list[str]]


def _models_path() -> str:
    return os.environ.get("AI_MODELS_CONFIG") or str(REPO_ROOT / "config" / "models.json")


def seed_tiers() -> dict[str, Any]:
    """The router section of the deployment's models.json, as written (no environment overlay)."""
    with open(_models_path(), encoding="utf-8") as handle:
        return json.load(handle)["router"]


def seed_profiles() -> dict[str, Any]:
    from meta_harness.run_service.routing import PROFILES_PATH

    return json.loads(PROFILES_PATH.read_text(encoding="utf-8"))


def seed_repos() -> dict[str, Any]:
    from meta_harness.planner.repos import registry_path

    return json.loads(registry_path().read_text(encoding="utf-8"))


def check_tiers(document: dict[str, Any], others: dict[str, dict[str, Any]]) -> list[str]:
    try:
        config = load_router_config(json.dumps(document))
    except ValueError as err:
        return [f"the router does not parse: {err}"]
    reasons = []
    if config.default_tier is None:
        reasons.append("default_tier is required: the router needs a tier for a task the classifier cannot place")
    profiles = (others.get("profiles") or {}).get("profiles", {})
    for tier in config.tiers:
        thinking = getattr(tier.thinking, "value", tier.thinking)
        if thinking is not None and thinking not in THINKING_LEVELS:
            reasons.append(f"tier {tier.name}: thinking {thinking} is not one the HTTP Gateway accepts")
        if not tier.candidates:
            reasons.append(f"tier {tier.name} has no candidates")
        for candidate in tier.candidates:
            label = f"{candidate.engine}:{candidate.model}"
            profile, slash, inner = candidate.model.partition("/")
            engine, colon, model = inner.partition(":")
            if candidate.engine != "harness" or not (slash and colon and profile and engine and model):
                reasons.append(f"tier {tier.name}: {label} is not harness:<profile>/<engine>:<model>")
            elif profile not in profiles:
                reasons.append(f"tier {tier.name}: {label} names harness profile {profile}, which the profiles lack")
            elif why := replaced_engine_reason(label, engine):
                reasons.append(f"tier {tier.name}: {why}")
    return reasons


def check_profiles(document: dict[str, Any], others: dict[str, dict[str, Any]]) -> list[str]:
    from meta_harness.run_service.routing import profile_errors

    profiles = document.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        return ["profiles must be an object of at least one harness profile, by name"]
    reasons = []
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            reasons.append(f"{name}: a harness profile is an object")
            continue
        reasons += profile_errors(name, profile)
        unknown = [t for t in profile.get("tools") or [] if t not in KNOWN_TOOLS]
        if unknown:
            reasons.append(f"{name}: unknown tools {', '.join(unknown)}")
    # A profile the active tiers still name cannot go.
    tiers = others.get("tiers") or {}
    for tier in tiers.get("tiers", []):
        for model in tier.get("models", []):
            name = str(model).removeprefix("harness:").split("/", 1)[0]
            if name not in profiles:
                reasons.append(f"tier {tier.get('name')} still names harness profile {name}")
    return reasons


def check_repos(document: dict[str, Any], others: dict[str, dict[str, Any]]) -> list[str]:
    repos = document.get("repos")
    if not isinstance(repos, dict):
        return ["repos must be an object of repos, by owner/name"]
    reasons = []
    for name, spec in repos.items():
        if name.count("/") != 1 or not all(name.split("/")):
            reasons.append(f"{name}: a repo is owner/name")
        if not isinstance(spec, dict):
            reasons.append(f"{name}: a repo's entry is an object")
            continue
        if spec.get("tracker", "github") not in TRACKERS:
            reasons.append(f"{name}: tracker must be one of {', '.join(sorted(TRACKERS))}")
        checks = spec.get("default_checks", [])
        if not isinstance(checks, list) or not all(isinstance(c, str) and c for c in checks):
            reasons.append(f"{name}: default_checks must be a list of commands")
    return reasons


KINDS: dict[str, Kind] = {
    "tiers": Kind(
        "tiers",
        "Router tiers",
        "The ai-api-unified router's tiers, each with its ordered harness-profile-and-model candidates and thinking level.",
        seed_tiers,
        check_tiers,
    ),
    "profiles": Kind(
        "profiles",
        "Harness profiles",
        "Each harness profile's tools, turn and minute limits, sandbox resources and bash allowlist.",
        seed_profiles,
        check_profiles,
    ),
    "repos": Kind(
        "repos",
        "Repo registry",
        "The repos goals may change, with each one's tracker and default acceptance checks.",
        seed_repos,
        check_repos,
    ),
}
