#!/usr/bin/env python3
"""Score a running Canopy's search against labelled queries, and print JSON.

    GATEWAY_URL=http://127.0.0.1:8080 CANOPY_TOKEN_DIR=<data>/tokens \
        uv run scripts/eval-search.py tests/fixtures/search.json

The file holds {"queries": [{"query": "...", "expected": ["skill:plugin/name", ...]}]}. A query counts
at the rank of the first expected id. Keep queries that name private skills in your own agent-data,
and pass that path instead.
"""
from __future__ import annotations

import json
import os
import sys
import time

from canopy import gateway as gw


def main(path: str, limit: int = 6) -> None:
    queries = json.load(open(path))["queries"]
    client = gw.McpClient(f"{gw.GATEWAY_URL}/mcp", timeout=30, name="canopy-eval")
    ranks, misses, degraded, seconds = [], [], set(), []
    for q in queries:
        started = time.time()
        reply = client.call("catalogue__search", {"query": q["query"], "limit": limit})
        seconds.append(time.time() - started)
        out = json.loads("".join(c.get("text", "") for c in reply.get("content", [])))
        degraded.update(out.get("degraded", []))
        ids = [r["id"] for r in out["results"]]
        rank = next((i + 1 for i, rid in enumerate(ids) if rid in q["expected"]), None)
        ranks.append(rank)
        if rank != 1:
            misses.append({"query": q["query"], "rank": rank, "got": ids[:3]})
    print(json.dumps({
        "queries": len(ranks), "first": sum(r == 1 for r in ranks),
        "top3": sum(r is not None and r <= 3 for r in ranks), "top6": sum(r is not None for r in ranks),
        "mrr": round(sum(1 / r for r in ranks if r) / len(ranks), 3),
        "median_ms": round(sorted(seconds)[len(seconds) // 2] * 1000),
        "degraded": sorted(degraded), "not_first": misses}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], int(os.environ.get("EVAL_LIMIT", "6")))
