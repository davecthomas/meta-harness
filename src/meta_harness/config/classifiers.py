"""The router classifier's engines and their models, from ai-api-unified, for the tier form's classifier fields.

The engines are those ai-api-unified's provider registry lists for
classification, the engines AiRouteClassifier can build, and `none`, which
routes without a classifier. Each engine's models come from the library's
model registry, by the provider the engine reaches: `openai-decisions` takes
the registry's Decisions models, the structured-output engines the priced
completions models of their provider. An engine whose models the registry
cannot know, such as `openai-compatible`, whose server decides, lists none,
so its model is typed.
"""

from typing import Any

from ai_api_unified.ai_provider_registry import (
    AI_PROVIDER_CAPABILITY_CLASSIFICATION,
    DICT_TUPLE_AI_PROVIDER_REGISTRY,
)
from ai_api_unified.pricing.pricing_registry import DICT_MODEL_INFO
from ai_api_unified.routing.router_config import STR_ROUTER_CLASSIFIER_NONE

DECISIONS_ENGINE = "openai-decisions"
DECISIONS_PREFIX = "decisions/"
# The registry provider each structured-output engine reaches, and the text a model's name must hold there:
# `anthropic` is Anthropic's models on Bedrock.
PROVIDER_FOR_ENGINE: dict[str, tuple[str, str]] = {
    "openai": ("openai", ""),
    "openai-responses": ("openai", ""),
    "claude": ("anthropic", ""),
    "google-gemini": ("google", ""),
    "bedrock": ("bedrock", ""),
    "anthropic": ("bedrock", "anthropic."),
}


def _models(engine: str) -> list[str]:
    """The models the registry names for one classifier engine, by name."""
    found = set()
    for (provider, model), info in DICT_MODEL_INFO.items():
        if engine == DECISIONS_ENGINE:
            if provider == "openai" and model.startswith(DECISIONS_PREFIX):
                found.add(model.removeprefix(DECISIONS_PREFIX))
            continue
        if engine not in PROVIDER_FOR_ENGINE:
            continue
        want, holds = PROVIDER_FOR_ENGINE[engine]
        rates = info.pricing.token_rates if info.pricing is not None else None
        # No output price: an embedding or image model. A "decisions/" model is the Decisions engine's.
        if provider != want or holds not in model or rates is None or rates.output_per_1m is None or "/" in model:
            continue
        found.add(model)
    return sorted(found)


def classifier_engines() -> list[dict[str, Any]]:
    """Every engine the router's classifier can name, by name, each with its models."""
    engines = sorted(
        {
            spec.str_engine
            for (capability, _), spec in DICT_TUPLE_AI_PROVIDER_REGISTRY.items()
            if capability == AI_PROVIDER_CAPABILITY_CLASSIFICATION
        }
    )
    listed = [{"engine": engine, "models": _models(engine)} for engine in engines]
    return listed + [{"engine": STR_ROUTER_CLASSIFIER_NONE, "models": []}]
