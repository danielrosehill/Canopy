"""Ranking for catalogue search: a keyword index, fusion, and the hosted rerank step.

No stack needed to test it: the index is an embedded library and `rerank` makes the one HTTP call.
Why each part has this shape is measured in docs/design.md, under "Retrieval pipeline".
"""
from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Iterable

import httpx
import tantivy

FIELD_WEIGHTS = {"name": 10.0, "description": 4.0, "plugin": 2.0, "body": 1.0}
RRF_K = 60
VECTOR_WEIGHT = float(os.environ.get("SEARCH_VECTOR_WEIGHT", "3"))

RERANK_MODEL = os.environ.get("RERANK_MODEL", "")   # empty: off, and nothing leaves the host
RERANK_URL = os.environ.get("RERANK_URL", "https://openrouter.ai/api/v1").rstrip("/")
RERANK_KEY = os.environ.get("RERANK_API_KEY", "")
RERANK_TIMEOUT = float(os.environ.get("RERANK_TIMEOUT_SEC", "2"))
RERANK_CANDIDATES = 20

_WORD = re.compile(r"[^\W_]+")


class KeywordIndex:
    """BM25 with weighted fields and English stemming, on Tantivy, in RAM.

    `update` changes only the records it is given, and a search during it reads the last commit.
    Tantivy does this work outside the Python lock, so a search does not wait for it. There is
    nothing on disk to go stale: a restart builds it again from Postgres, in about a second."""

    WRITER_HEAP = 30_000_000   # bytes; Tantivy's minimum is 15 MB per thread

    def __init__(self, records: Iterable[dict] = ()):
        schema = tantivy.SchemaBuilder()
        schema.add_text_field("id", stored=True, tokenizer_name="raw")
        for field in FIELD_WEIGHTS:
            schema.add_text_field(field, stored=False, tokenizer_name="en_stem")
        self.index = tantivy.Index(schema.build())
        self.update(records)

    def __len__(self) -> int:
        return self.index.searcher().num_docs

    def update(self, records: Iterable[dict] = (), removed: Iterable[str] = ()) -> None:
        """Add or replace `records` and drop the ids in `removed`, in one commit. Callers take
        turns: Tantivy allows one writer at a time."""
        writer = self.index.writer(self.WRITER_HEAP, 1)
        for rid in removed:
            writer.delete_documents_by_term("id", rid)
        for r in records:
            writer.delete_documents_by_term("id", r["id"])
            doc = tantivy.Document()
            doc.add_text("id", r["id"])
            for field in FIELD_WEIGHTS:
                doc.add_text(field, r.get(field) or "")
            writer.add_document(doc)
        writer.commit()
        writer.wait_merging_threads()
        self.index.reload()

    def search(self, query: str, limit: int = 200) -> list[str]:
        # Bare lowercase words only, so nothing in a query can reach the query language.
        words = [w for w in _WORD.findall(query.lower()) if len(w) > 1]
        if not words:
            return []
        parsed = self.index.parse_query(" ".join(words), list(FIELD_WEIGHTS), field_boosts=FIELD_WEIGHTS)
        searcher = self.index.searcher()
        return [searcher.doc(address)["id"][0] for _, address in searcher.search(parsed, limit).hits]


def fuse(keywords: list[str], vectors: list[str], vector_weight: float = VECTOR_WEIGHT,
         unembedded: Iterable[str] = ()) -> dict[str, float]:
    """Reciprocal rank fusion, with vectors counted `vector_weight` times. A record with no vector yet
    cannot appear in `vectors`, so its keyword rank stands in. Otherwise a first-place keyword match
    falls behind about 120 vector hits, and a skill vanishes from the moment it is edited until it is embedded."""
    scores: Counter = Counter()
    for weight, ranking in ((1.0, keywords), (vector_weight, vectors)):
        for rank, rid in enumerate(ranking):
            scores[rid] += weight / (RRF_K + rank)
    missing = set(unembedded)
    for rank, rid in enumerate(keywords):
        if rid in missing:
            scores[rid] += vector_weight / (RRF_K + rank)
    return dict(scores.most_common())


def rerank(query: str, candidates: list[dict], post=httpx.post, timeout: float | None = None) -> list[str]:
    """The candidates' ids, best first. Raises on any failure, and the caller keeps the fused
    order. Sends the query and each candidate's name and description to the provider."""
    docs = [f"{c['name']}: {c['description'] or ''}" for c in candidates]
    r = post(f"{RERANK_URL}/rerank", json={"model": RERANK_MODEL, "query": query, "documents": docs},
             headers={"Authorization": f"Bearer {RERANK_KEY}"} if RERANK_KEY else {},
             timeout=RERANK_TIMEOUT if timeout is None else timeout)
    r.raise_for_status()
    order = [candidates[x["index"]]["id"] for x in r.json()["results"]]
    if sorted(order) != sorted(c["id"] for c in candidates):
        raise RuntimeError(f"reranker returned {len(order)} of {len(candidates)} candidates")
    return order
