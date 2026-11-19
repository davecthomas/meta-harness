"""The router classifier's engines and models, as the tier form offers them."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from meta_harness.config import api as config_api


def _no_pool():
    raise AssertionError("listing the classifier engines reads no configuration")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(config_api.router(_no_pool, lambda: "op@example.com", events=None))
    return TestClient(app)


def test_the_classifier_engines_are_the_librarys_each_with_its_models():
    response = _client().get("/v1/config/classifiers")
    assert response.status_code == 200
    engines = {e["engine"]: e["models"] for e in response.json()["engines"]}

    assert {"openai-decisions", "openai", "openai-responses", "claude", "google-gemini", "none"} <= set(engines)
    assert engines["openai-decisions"] and all("/" not in m for m in engines["openai-decisions"])
    assert engines["claude"] and all(m.startswith("claude-") for m in engines["claude"])
    assert engines["google-gemini"] and all(m.startswith("gemini-") for m in engines["google-gemini"])
    assert engines["anthropic"] and all("anthropic." in m for m in engines["anthropic"])
    assert not any("embedding" in m for models in engines.values() for m in models)
    assert engines["none"] == [], "none turns the classifier off, so it has no model"
    assert engines["openai-compatible"] == [], "its server decides its models, so the model is typed"
    for models in engines.values():
        assert models == sorted(models)
