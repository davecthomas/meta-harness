"""Embedding vectors for texts, from the HTTP Gateway's /v1/embeddings."""

from __future__ import annotations

import os

import httpx

from meta_harness import retrying

# The most texts one call embeds: the gateway refuses more.
MAX_INPUTS = 256


class EmbeddingError(RuntimeError):
    """The texts could not be embedded. The message says why."""


async def embed(
    texts: list[str], engine: str, model: str, transport: httpx.AsyncBaseTransport | None = None
) -> list[list[float]]:
    """One vector for each text, in the texts' order.

    Raises:
        EmbeddingError: The gateway is not set up, could not be reached, or refused the call.
    """
    if not texts:
        return []
    url = os.environ.get("GATEWAY_URL", "").rstrip("/")
    key = os.environ.get("GATEWAY_API_KEY", "")
    if not (url and key):
        raise EmbeddingError("set GATEWAY_URL and GATEWAY_API_KEY in the planner's environment")
    found: list[list[float]] = []
    async with httpx.AsyncClient(timeout=30, transport=retrying.async_transport(transport)) as http:
        for start in range(0, len(texts), MAX_INPUTS):
            batch = texts[start : start + MAX_INPUTS]
            body = {"engine": engine, "model": model, "inputs": batch}
            try:
                # A call that embeds the same texts again is harmless, so it is safe to repeat.
                response = await http.post(
                    f"{url}/v1/embeddings", json=body, headers={"Authorization": f"Bearer {key}"}
                )
            except httpx.HTTPError as err:
                raise EmbeddingError(f"the HTTP Gateway is unreachable: {err}") from err
            if response.status_code != 200:
                raise EmbeddingError(f"the HTTP Gateway answered {response.status_code}: {response.text[:200]}")
            vectors = sorted(response.json().get("vectors") or [], key=lambda v: v["index"])
            if len(vectors) != len(batch):
                raise EmbeddingError(f"the HTTP Gateway returned {len(vectors)} vectors for {len(batch)} texts")
            found += [list(v["embedding"]) for v in vectors]
    return found
