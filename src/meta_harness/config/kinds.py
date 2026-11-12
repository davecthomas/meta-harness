"""The configuration kinds: where each one's first version comes from, and how a new version is checked.

- `tiers`: the ai-api-unified router's `router` section, as in models.json. Its
  tier candidates are harness pairs, `harness:<profile>/<engine>:<model>`.
- `profiles`: the harness profiles, as in config/harness_profiles.json.
- `repos`: the repo registry, as in config/repos.example.json.
- `task_policy`: the task policy, as in config/task_policy.json: how many tasks
  run at once (`max_parallel`), the review loop's reviewers and rounds, and the
  spend limit a task gets for its issue's size (`max_usd_by_size`).
- `skills`: the skills a harness profile may name, each with its files
  (skills.py), seeded from config/skills/.

A kind's first version is seeded from the deployment's file. A new version is
refused, with every reason, when the service that reads it would refuse it at
startup: a malformed router, a candidate naming a profile that does not exist,
a profile with no tools, a repo with no tracker, more parallel tasks than the
VM holds.
"""

import json
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from ai_api_unified.routing.router_config import load_router_config

from meta_harness.config.skills import check_skills, seed_skills
from meta_harness.run_service.routing import replaced_engine_reason

from meta_harness.run_service.paths import REPO_ROOT

KNOWN_TOOLS = {"read", "write", "edit", "ls", "find", "grep", "bash", "submit_review", "update_plan", "create_issue"}
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


def seed_task_policy() -> dict[str, Any]:
    from meta_harness.run_service.routing import TASK_POLICY_PATH

    return json.loads(TASK_POLICY_PATH.read_text(encoding="utf-8"))


SANDBOXES_ENV = "MH_MAX_SANDBOXES"


def sandbox_ceiling() -> int | None:
    """The most per-run sandboxes this deployment's machine holds (MH_MAX_SANDBOXES), so the most tasks that can
    run at once; None when the deployment sets no ceiling, as on a laptop.

    Raises:
        ConfigError: MH_MAX_SANDBOXES is set but is not a positive whole number.
    """
    raw = os.environ.get(SANDBOXES_ENV, "").strip()
    if not raw:
        return None
    if not raw.isdigit() or int(raw) < 1:
        raise ConfigError([f"{SANDBOXES_ENV} must be a positive whole number, not {raw!r}"])
    return int(raw)


# What a budget does across the deployment. block: a goal's budget and a task's spend limit stop work, as each
# says. track: nothing is held, refused or ended for either; spend is still recorded and shown against them.
BUDGET_POLICIES = ("block", "track")
# The highest ceiling a task policy may set on one task's spend. A ceiling is a safety limit, so it cannot be
# set out of reach.
MAX_TASK_USD = Decimal("1000")
# The sizes the planner gives an issue before it becomes a task (meta_harness.planner.sizing).
ISSUE_SIZES = ("small", "medium", "large")


def check_size_limits(limits: Any) -> list[str]:
    """Problems with a task policy's max_usd_by_size: an object from issue size to a positive dollar amount, as a
    string or a number. Left out, or a size left out: a task of that size gets no spend limit from its size."""
    if limits is None:
        return []
    if not isinstance(limits, dict):
        return ["max_usd_by_size must be an object from issue size (small, medium, large) to a dollar amount"]
    reasons = []
    for size, amount in limits.items():
        if size not in ISSUE_SIZES:
            reasons.append(f"max_usd_by_size: {size} is not an issue size; use {', '.join(ISSUE_SIZES)}")
            continue
        try:
            positive = not isinstance(amount, bool) and Decimal(str(amount)) > 0
        except (InvalidOperation, ValueError):
            positive = False
        if not positive:
            reasons.append(f'max_usd_by_size.{size} must be a dollar amount above 0, such as "5.00"')
    return reasons


def check_task_policy(document: dict[str, Any], others: dict[str, dict[str, Any]] | None = None) -> list[str]:
    """Problems with a task policy. Reviewer pairs are checked against the profiles when others holds them."""
    reasons = []
    parallel = document.get("max_parallel")
    if isinstance(parallel, bool) or not isinstance(parallel, int) or parallel < 1:
        reasons.append("max_parallel must be a positive whole number: the most tasks that run at once")
    else:
        try:
            ceiling = sandbox_ceiling()
        except ConfigError as err:
            ceiling = None
            reasons += err.reasons
        if ceiling is not None and parallel > ceiling:
            reasons.append(f"max_parallel {parallel} is more than the {ceiling} sandboxes this deployment's VM holds")
    review = document.get("review")
    if not isinstance(review, dict):
        return reasons + ["review must be an object with reviewers and max_rounds"]
    reviewers, max_rounds = review.get("reviewers"), review.get("max_rounds")
    if not (isinstance(reviewers, list) and reviewers and all(isinstance(r, str) for r in reviewers)):
        reasons.append("review.reviewers must be a non-empty list of pairs")
        reviewers = []
    if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 0:
        reasons.append("review.max_rounds must be a whole number, 0 or more")
    hold = review.get("hold_minutes")
    if hold is not None and (isinstance(hold, bool) or not isinstance(hold, int) or hold < 0):
        reasons.append("review.hold_minutes must be a whole number of minutes, 0 or more (0: fix runs start at once)")
    budget = document.get("budget_policy")
    if budget is not None and budget not in BUDGET_POLICIES:
        reasons.append(
            f"budget_policy must be one of {', '.join(BUDGET_POLICIES)}: block stops work at a budget or spend "
            "limit, and track only records the spend against it"
        )
    ceiling = document.get("max_task_usd")
    if ceiling is not None:
        try:
            amount = Decimal(str(ceiling))
            usable = not isinstance(ceiling, bool) and amount.is_finite() and 0 < amount <= MAX_TASK_USD
        except InvalidOperation:
            usable = False
        if not usable:
            reasons.append(
                f"max_task_usd must be an amount above 0 and at most {MAX_TASK_USD}: the most one task may "
                "spend, whatever its own limit or the budget policy says"
            )
    writing = document.get("writing_pass")
    if writing is not None and not isinstance(writing, bool):
        reasons.append(
            "writing_pass must be true or false: false posts what a run wrote without the writing pass's "
            "check and rewrite"
        )
    wait = document.get("merge_wait_minutes")
    if wait is not None and (isinstance(wait, bool) or not isinstance(wait, int) or wait < 0):
        reasons.append("merge_wait_minutes must be a whole number of minutes, 0 or more (0: merge without waiting)")
    reasons += check_size_limits(document.get("max_usd_by_size"))
    if others is not None:
        profiles = (others.get("profiles") or {}).get("profiles", {})
        for pair in reviewers:
            profile, slash, inner = pair.removeprefix("harness:").partition("/")
            if not pair.startswith("harness:") or not slash or ":" not in inner:
                reasons.append(f"reviewer {pair} is not harness:<profile>/<engine>:<model>")
            elif profile not in profiles:
                reasons.append(f"reviewer {pair} names harness profile {profile}, which the profiles lack")
    return reasons


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
        named = profile.get("skills")
        if named is not None and not (isinstance(named, list) and all(isinstance(s, str) for s in named)):
            reasons.append(f"{name}: skills must be a list of skill names, or left out")
        elif named:
            known = (others.get("skills") or {}).get("skills", {})
            missing = [s for s in named if s not in known]
            if missing:
                reasons.append(f"{name}: no skill named {', '.join(missing)}; import it on the Skills page first")
            if "read" not in (profile.get("tools") or []):
                reasons.append(f"{name}: a profile with skills needs the read tool, which is how a run opens one")
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
    "skills": Kind(
        "skills",
        "Skills",
        "Packaged instructions, with their scripts and references, that a harness profile's runs load.",
        seed_skills,
        check_skills,
    ),
    "task_policy": Kind(
        "task_policy",
        "Task policy",
        "How many tasks run at once across every goal, whether budgets stop work or only track spend, the "
        "review loop's reviewer pairs and fix rounds, and the spend limit a task gets for its issue's size.",
        seed_task_policy,
        check_task_policy,
    ),
}
