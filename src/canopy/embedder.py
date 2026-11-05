"""The one route to embeddings, behind `POST /v1/embeddings`.

`EMBED_PROVIDER` picks the primary and `EMBED_FALLBACK` an optional second. Vectors are labelled with
the canonical model name whichever route made them, and `system.model_map` says what each
provider calls that model.
"""
from __future__ import annotations

import os
from collections.abc import Callable

import httpx

PRIMARY = os.environ.get("EMBED_PROVIDER", "")
FALLBACK = os.environ.get("EMBED_FALLBACK", "")
QUERY_BUDGET = float(os.environ.get("EMBED_QUERY_BUDGET_SEC", "6"))   # a person is waiting
BULK_TIMEOUT = float(os.environ.get("EMBED_BULK_TIMEOUT_SEC", "120"))


def routes() -> dict[str, tuple[str, str]]:
    return {"local": (os.environ.get("OLLAMA_URL", "http://ollama:11434/v1"), ""),
            "openrouter": ("https://openrouter.ai/api/v1", os.environ.get("OPENROUTER_API_KEY", ""))}


class EmbedError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Embedder:
    def __init__(self, primary: str = PRIMARY, fallback: str = FALLBACK,
                 provider_model: Callable[[str, str], str] = lambda model, provider: model,
                 post=httpx.post):
        self.chain = [p for p in (primary, fallback) if p]
        unknown = set(self.chain) - set(routes())
        if unknown:
            raise ValueError(f"unknown embedding provider: {sorted(unknown)}")
        self.provider_model, self.post = provider_model, post

    def embed(self, model: str, texts: list[str]) -> dict:
        """An OpenAI-style reply, plus `route`: the provider that answered."""
        if not self.chain:
            raise EmbedError(503, "no embedding provider: set EMBED_PROVIDER")
        timeout = QUERY_BUDGET if len(texts) == 1 else BULK_TIMEOUT
        failures = []
        for provider in self.chain:
            url, key = routes()[provider]
            try:
                r = self.post(f"{url.rstrip('/')}/embeddings", timeout=timeout,
                              json={"model": self.provider_model(model, provider), "input": texts},
                              headers={"Authorization": f"Bearer {key}"} if key else {})
                r.raise_for_status()
                doc = r.json()
                if len(doc.get("data") or []) != len(texts):
                    raise ValueError(f"{len(doc.get('data') or [])} vectors for {len(texts)} inputs")
                return {"object": "list", "model": model, "route": provider, "data": doc["data"],
                        "usage": doc.get("usage")}
            except (httpx.HTTPError, ValueError, KeyError) as e:
                failures.append(f"{provider}: {e}")
        raise EmbedError(502, "; ".join(failures))
