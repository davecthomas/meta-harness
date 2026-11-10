"""The models a tier candidate can name, from ai-api-unified's model registry, for the tier form editor.

The registry catalogs models by provider. A harness candidate names an
engine, so each provider maps to the engine a harness reaches it through:
OpenAI's through `openai-responses`, since every harness turn sends tools
with a reasoning level (meta_harness.run_service.routing.REPLACED_ENGINES).
Only completions models with token prices are offered: the gateway refuses
a run grant on a model it cannot price.
"""

from typing import Any

from ai_api_unified.pricing.pricing_registry import DICT_MODEL_INFO

# The engine a harness candidate uses for each registry provider.
ENGINE_FOR_PROVIDER = {"anthropic": "claude", "openai": "openai-responses", "google": "google-gemini"}


def catalog() -> list[dict[str, Any]]:
    """Every priced completions model, as `<engine>:<model>` with its lifecycle status, by engine then name."""
    found = []
    for (provider, model), info in DICT_MODEL_INFO.items():
        engine = ENGINE_FOR_PROVIDER.get(provider)
        rates = info.pricing.token_rates if info.pricing is not None else None
        # No output price: an embedding or image model. A "decisions/" model is a classifier.
        if not engine or rates is None or rates.output_per_1m is None or "/" in model:
            continue
        found.append(
            {
                "name": f"{engine}:{model}",
                "engine": engine,
                "model": model,
                "status": info.status.value,
                "input_per_1m": str(rates.input_per_1m),
                "output_per_1m": str(rates.output_per_1m),
            }
        )
    return sorted(found, key=lambda m: (m["engine"], m["model"]))
