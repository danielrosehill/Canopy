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
# Optional: answers single-text requests first, so bulk can run on a hosted route while a person waits on
# a local one. Measured 2026-11-05: 60-100 ms local, against 1.3 s median hosted, for one query.
QUERY_FIRST = os.environ.get("EMBED_QUERY_PROVIDER", "")
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
    def __init__(self, primary: str = PRIMARY, fallback: str = FALLBACK, query_first: str = QUERY_FIRST,
                 provider_model: Callable[[str, str], str] = lambda model, provider: model,
                 post=httpx.post):
        self.chain = [p for p in (primary, fallback) if p]
        self.query_chain = [query_first, *(p for p in self.chain if p != query_first)] if query_first else self.chain
        unknown = set(self.query_chain) - set(routes())
        if unknown:
            raise ValueError(f"unknown embedding provider: {sorted(unknown)}")
        self.provider_model, self.post = provider_model, post

    def embed(self, model: str, texts: list[str]) -> dict:
        """An OpenAI-style reply, plus `route`: the provider that answered."""
        if not self.chain:
            raise EmbedError(503, "no embedding provider: set EMBED_PROVIDER")
        query = len(texts) == 1
        chain = self.query_chain if query else self.chain
        timeout = QUERY_BUDGET / len(chain) if query else BULK_TIMEOUT   # leave the next route time to answer
        failures = []
        for provider in chain:
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
