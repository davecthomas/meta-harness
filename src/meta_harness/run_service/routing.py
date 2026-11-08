"""Harness profiles, tier candidates, and the ai-api-unified router the route activity calls.

The router's tiers live in config/models.json under `router`, as the design's
principle 1 asks: code names no model and no harness profile. Each tier
candidate is a harness-profile-and-model pair,

    harness:<harness profile>/<engine>:<model>

which the ai-api-unified router reads as engine `harness` and returns as it
is (ai-api-unified 3.30.0, AiRouteClassifier). This module splits it, and
looks the harness profile up in config/harness_profiles.json.

The worker calls validate() at startup, so a candidate naming an unknown
harness profile or a malformed pair stops the worker instead of failing a
task (principle 4). The worker builds one AiRouteClassifier and every route
activity shares it.

With MH_CONFIG_SOURCE=db, as the cloud deployment sets it, the tiers and the
profiles are instead the active versions an operator edits in the console
(meta_harness.config), seeded from those files. Every route activity reads the
active version, so a saved edit applies to the next task routed, and a
running task keeps what it started with.

Environment: AI_MODELS_CONFIG (the models.json path), AI_MODELS_ENV (one of
its `environments`), HARNESS_PROFILES (default config/harness_profiles.json),
HARNESS_IMAGE (the image a harness profile with no image of its own runs),
MH_CONFIG_SOURCE (file, the default, or db).
"""

import json
import os
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

from ai_api_unified import AIFactory, AiRouteClassifier
from ai_api_unified.routing.router_config import load_router_config

from meta_harness.run_service.paths import REPO_ROOT
from meta_harness.run_service.shared import Candidate

PROFILES_PATH = Path(os.environ.get("HARNESS_PROFILES") or REPO_ROOT / "config" / "harness_profiles.json")
DEFAULT_IMAGE = os.environ.get("HARNESS_IMAGE", "harness-pi:dev")
HARNESS_ENGINE = "harness"


class CandidateError(ValueError):
    """A tier candidate or pin that names no harness profile, or is not a harness pair."""


def _from_database() -> bool:
    from meta_harness.config import store

    return store.from_database()


def profiles() -> dict[str, dict[str, Any]]:
    """The harness profiles: the active configuration version, or config/harness_profiles.json."""
    if _from_database():
        from meta_harness.config import store

        return dict(store.active_document("profiles")[1]["profiles"])
    return _file_profiles()


@cache
def _file_profiles() -> dict[str, dict[str, Any]]:
    loaded = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    return dict(loaded["profiles"])


def candidate(engine: str, model: str) -> Candidate:
    """A router candidate (engine `harness`, model `<profile>/<engine>:<model>`) as a Candidate.

    Raises:
        CandidateError: The engine is not `harness`, the pair is malformed, or
            the harness profile is unknown.
    """
    label = f"{engine}:{model}"
    if engine != HARNESS_ENGINE:
        raise CandidateError(f"{label} is not a harness candidate: write harness:<profile>/<engine>:<model>")
    profile_name, slash, inner = model.partition("/")
    inner_engine, colon, inner_model = inner.partition(":")
    if not (slash and colon and profile_name and inner_engine and inner_model):
        raise CandidateError(f"{label} is malformed: write harness:<profile>/<engine>:<model>")
    known = profiles()
    if profile_name not in known:
        raise CandidateError(f"{label} names harness profile {profile_name}, which the harness profiles lack")
    profile = {"name": profile_name, **known[profile_name]}
    profile["image"] = profile.get("image") or DEFAULT_IMAGE
    return Candidate(label=label, profile_name=profile_name, engine=inner_engine, model=inner_model, profile=profile)


def pinned(pin: str) -> Candidate:
    """A pin, harness:<profile>/<engine>:<model>, as a Candidate."""
    engine, _, model = pin.partition(":")
    return candidate(engine.strip(), model.strip())


def router() -> AiRouteClassifier:
    """The classify-only ai-api-unified router: over the active tiers version, or models.json's `router`."""
    if _from_database():
        from meta_harness.config import store

        version, document = store.active_document("tiers")
        return _versioned_router(version, json.dumps(document, sort_keys=True))
    return _file_router()


@cache
def _file_router() -> AiRouteClassifier:
    return AIFactory.get_route_classifier()


@lru_cache(maxsize=4)
def _versioned_router(version: int, document: str) -> AiRouteClassifier:
    """One router per tiers version, built once and shared by every route activity that sees that version."""
    return AIFactory.get_route_classifier(router=load_router_config(document))


THINKING_LEVELS = {"off", "minimal", "low", "medium", "high", "max"}


def profile_errors(name: str, profile: dict[str, Any]) -> list[str]:
    """What is wrong with one harness profile, as the harness server would refuse it at its first run."""
    errors = []
    tools = profile.get("tools")
    if not (isinstance(tools, list) and tools and all(isinstance(t, str) for t in tools)):
        errors.append(f"{name}: tools must be a non-empty list of tool names")
    for key in ("max_turns", "max_minutes"):
        value = profile.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            errors.append(f"{name}: {key} must be a positive number")
    allow = profile.get("bash_allow")
    if allow is not None and not (isinstance(allow, list) and allow and all(isinstance(p, str) and p for p in allow)):
        errors.append(f"{name}: bash_allow must be a non-empty list of program names, or left out")
    resources = profile.get("resources")
    if resources is not None:
        for key in ("cpu", "memory_gb"):
            value = resources.get(key) if isinstance(resources, dict) else None
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                errors.append(f"{name}: resources.{key} must be a positive number")
    return errors


def validate() -> list[str]:
    """Checks every harness profile and tier candidate, and returns one line per tier for the worker's log.

    Raises:
        CandidateError: A harness profile is malformed, or a tier candidate is not a valid harness pair.
        ValueError: models.json has no router, or it names no default_tier.
    """
    errors = [error for name, profile in profiles().items() for error in profile_errors(name, profile)]
    if errors:
        raise CandidateError(f"{PROFILES_PATH.name}: " + "; ".join(errors))
    lines = []
    config = router().config
    for tier in config.tiers:
        names = [candidate(c.engine, c.model).label for c in tier.candidates]
        thinking = getattr(tier, "thinking", None)
        level = getattr(thinking, "value", thinking)
        if level is not None and level not in THINKING_LEVELS:
            raise CandidateError(f"tier {tier.name}: thinking {level} is not one the HTTP Gateway accepts")
        lines.append(f"tier {tier.name}: {', '.join(names)}" + (f", thinking {level}" if level else ""))
    classifier = router().classifier
    lines.append(
        f"classifier: {classifier.model_name if classifier else 'none, so every task takes tier ' + str(config.default_tier)}"
    )
    return lines
