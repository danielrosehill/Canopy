"""Ranking for catalogue search: a keyword index in memory, fusion, and the hosted rerank step.

Pure code, apart from `rerank`'s one HTTP call, so it can be tested without a stack. Why each part
has this shape is measured in docs/design.md, under "Retrieval pipeline".
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter, defaultdict
from collections.abc import Iterable

import httpx
import numpy as np
import snowballstemmer

FIELD_WEIGHTS = {"name": 10.0, "description": 4.0, "plugin": 2.0, "body": 1.0}
K1, B = 1.2, 0.75
RRF_K = 60
VECTOR_WEIGHT = float(os.environ.get("SEARCH_VECTOR_WEIGHT", "3"))

RERANK_MODEL = os.environ.get("RERANK_MODEL", "")   # empty: off, and nothing leaves the host
RERANK_URL = os.environ.get("RERANK_URL", "https://openrouter.ai/api/v1").rstrip("/")
RERANK_KEY = os.environ.get("RERANK_API_KEY", "")
RERANK_TIMEOUT = float(os.environ.get("RERANK_TIMEOUT_SEC", "2"))
RERANK_CANDIDATES = 20

_WORD = re.compile(r"[^\W_]+")


class Tokenizer:
    """Stems each distinct word once, which is most of the cost of a build."""

    def __init__(self):
        self._stemmer = snowballstemmer.stemmer("english")
        self._stems: dict[str, str] = {}

    def __call__(self, text: str | None) -> list[str]:
        out = []
        for word in _WORD.findall((text or "").lower()):
            if len(word) > 1:
                stem = self._stems.get(word)
                if stem is None:
                    stem = self._stems[word] = self._stemmer.stemWord(word)
                out.append(stem)
        return out


class KeywordIndex:
    """BM25 with weighted fields. Immutable once built: a sync builds a new one and swaps it in,
    so search keeps using the previous one meanwhile."""

    def __init__(self, records: Iterable[dict]):
        self.tokenize = Tokenizer()
        self.ids: list[str] = []
        postings: dict[str, tuple[list[int], list[float]]] = defaultdict(lambda: ([], []))
        lengths = []
        for doc, r in enumerate(records):
            self.ids.append(r["id"])
            weighted: Counter = Counter()
            for field, weight in FIELD_WEIGHTS.items():
                for token in self.tokenize(r.get(field)):
                    weighted[token] += weight
            lengths.append(sum(weighted.values()))
            for token, tf in weighted.items():
                docs, tfs = postings[token]
                docs.append(doc)
                tfs.append(tf)
        self.lengths = np.asarray(lengths, dtype=np.float32)
        self.average = float(self.lengths.mean()) if lengths else 1.0
        self.postings = {t: (np.asarray(d, dtype=np.int32), np.asarray(f, dtype=np.float32))
                         for t, (d, f) in postings.items()}

    def __len__(self) -> int:
        return len(self.ids)

    def search(self, query: str, limit: int = 200) -> list[str]:
        scores = np.zeros(len(self.ids), dtype=np.float32)
        for token in set(self.tokenize(query)):
            if token not in self.postings:
                continue
            docs, tfs = self.postings[token]
            idf = math.log(1 + (len(self.ids) - len(docs) + 0.5) / (len(docs) + 0.5))
            norm = K1 * (1 - B + B * self.lengths[docs] / self.average)
            scores[docs] += idf * tfs * (K1 + 1) / (tfs + norm)
        hits = np.flatnonzero(scores)
        order = hits[np.argsort(-scores[hits], kind="stable")][:limit]
        return [self.ids[i] for i in order]


def fuse(keywords: list[str], vectors: list[str], vector_weight: float = VECTOR_WEIGHT) -> dict[str, float]:
    """Reciprocal rank fusion, with vectors counted `vector_weight` times."""
    scores: Counter = Counter()
    for weight, ranking in ((1.0, keywords), (vector_weight, vectors)):
        for rank, rid in enumerate(ranking):
            scores[rid] += weight / (RRF_K + rank)
    return dict(scores.most_common())


def rerank(query: str, candidates: list[dict], post=httpx.post) -> list[str]:
    """The candidates' ids, best first. Raises on any failure, and the caller keeps the fused
    order. Sends the query and each candidate's name and description to the provider."""
    docs = [f"{c['name']}: {c['description'] or ''}" for c in candidates]
    r = post(f"{RERANK_URL}/rerank", json={"model": RERANK_MODEL, "query": query, "documents": docs},
             headers={"Authorization": f"Bearer {RERANK_KEY}"} if RERANK_KEY else {}, timeout=RERANK_TIMEOUT)
    r.raise_for_status()
    order = [candidates[x["index"]]["id"] for x in r.json()["results"]]
    if sorted(order) != sorted(c["id"] for c in candidates):
        raise RuntimeError(f"reranker returned {len(order)} of {len(candidates)} candidates")
    return order
