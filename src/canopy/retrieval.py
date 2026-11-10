"""The search pipeline: every moving part of a search, in one place, in order.

    keywords + vectors -> fuse -> rerank -> shape -> log

`Search.run` is the only way in, for the MCP tool and for `POST /v1/search` alike. A stage marked
optional may fail, or run out of budget, and the search still answers: the reply says what fell
short in `degraded`, and the next stage works from what there is. To add a stage, add a method and
a line in `Search.__init__`. docs/design.md has the measurements behind each stage.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple

import numpy as np

from . import index as ix
from . import ranking

KINDS = ("plugin", "skill", "command", "agent", "mcp", "tool")
QUERY_EMBED_TIMEOUT = float(os.environ.get("QUERY_EMBED_TIMEOUT_SEC", "8"))
# Cosine of the best result, below which a reply says the match is weak. Fused rank scores cannot
# do this: every query has a first place. 0.60 is for bge-m3; see docs/design.md, "What the agent sees".
CONFIDENCE_MIN = float(os.environ.get("SEARCH_CONFIDENCE_MIN", "0.6"))
# Every result costs the agent context, so these are the levers on the size of a reply.
TOOL_HITS = int(os.environ.get("SEARCH_TOOL_HITS", "3"))
# Reranked, the top 3 holds almost everything the top 6 does. Fused alone it does not: docs/design.md.
SEARCH_LIMIT = int(os.environ.get("SEARCH_LIMIT") or (3 if ranking.RERANK_MODEL else 6))
TOOL_MIN_COSINE = float(os.environ.get("SEARCH_TOOL_MIN_COSINE", "0"))   # drop unrelated tools; 0 keeps all
SEARCH_DESC_CHARS = int(os.environ.get("SEARCH_DESC_CHARS", "300"))   # a shortlist; `read` has the full text
LOG_DAYS = int(os.environ.get("SEARCH_LOG_DAYS", "90"))   # 0 turns the log off
LOG_QUERY_CHARS = 500
REQUEST_FIELDS = {"query", "kind", "plugin", "marketplace", "limit", "full", "budget_ms", "trace"}


def _env_list(name: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


# A scoped catalogue: a second catalogue instance for another user, who must only see some of the
# marketplaces and some of the gateway's servers. Either variable set turns scoping on, and then
# anything not listed is invisible to search, list and read: plugin/skill/command/agent records
# need their plugin in a CATALOGUE_MARKETPLACES marketplace, tool and mcp records need their server
# in CATALOGUE_SERVERS. Unset (the default), the catalogue sees everything, as before.
SCOPE_MARKETPLACES = _env_list("CATALOGUE_MARKETPLACES")
SCOPE_SERVERS = _env_list("CATALOGUE_SERVERS")
SCOPED = bool(SCOPE_MARKETPLACES or SCOPE_SERVERS)


def scope() -> tuple[str, list] | None:
    """The WHERE clause (on alias r) that keeps a scoped catalogue to its records; None when unscoped."""
    if not SCOPED:
        return None
    return ("((r.kind NOT IN ('tool', 'mcp') AND r.plugin IN "
            "(SELECT name FROM plugins WHERE marketplace = ANY(%s))) "
            "OR (r.kind IN ('tool', 'mcp') AND split_part(r.name, '__', 1) = ANY(%s)))",
            [SCOPE_MARKETPLACES, SCOPE_SERVERS])


def filters(kind: str | None, plugin: str | None, marketplace: str | None) -> tuple[str, list]:
    where, args = [], []
    if kind:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        where.append("r.kind = %s"); args.append(kind)
    if plugin:
        where.append("r.plugin = %s"); args.append(plugin)
    if marketplace:
        where.append("r.plugin IN (SELECT name FROM plugins WHERE marketplace = %s)"); args.append(marketplace)
    if sc := scope():
        where.append(sc[0]); args.extend(sc[1])
    return (" AND ".join(where) or "1=1"), args


def summary(r) -> dict:
    return {"id": r["id"], "kind": r["kind"], "plugin": r["plugin"] or None,
            "name": r["name"], "description": r["description"]}


class Stage(NamedTuple):
    name: str
    run: Callable[["State"], None]
    fallback: str | None = None   # set: optional, and this is what `degraded` says when it fails


@dataclass
class State:
    """One search as it moves through the stages."""
    query: str
    kind: str | None
    full: bool
    limit: int
    where: str
    args: list
    deadline: float | None
    allowed: set[str] = field(default_factory=set)
    keyword_ids: list[str] = field(default_factory=list)
    vector_ids: list[str] = field(default_factory=list)
    cosine: dict[str, float] = field(default_factory=dict)
    unembedded: set[str] = field(default_factory=set)
    ranks: dict[str, float] = field(default_factory=dict)
    ordered: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    reply: dict = field(default_factory=dict)

    def left(self, most: float) -> float:
        """Seconds a stage may take: its own limit, or what is left of the budget if that is less."""
        if self.deadline is None:
            return most
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("out of budget")
        return min(most, left)


class Search:
    def __init__(self, db, keyword_index: Callable[[], ranking.KeywordIndex | None]):
        self.db, self.keyword_index = db, keyword_index
        self.pruned = 0.0
        self.stages = [
            Stage("filter", self._filter),
            Stage("keywords", self._keywords),
            Stage("vectors", self._vectors, "semantic search unavailable, keyword only: {}"),
            Stage("fuse", self._fuse),
            Stage("rerank", self._rerank, "reranking unavailable, fused order: {}"),
            Stage("shape", self._shape),
        ]

    def run(self, query: str, kind: str | None = None, plugin: str | None = None, marketplace: str | None = None,
            limit: int | None = None, full: bool = False, budget_ms: int | None = None,
            trace: bool = False) -> dict:
        where, args = filters(kind, plugin, marketplace)
        limit = max(1, min(SEARCH_LIMIT if limit is None else limit, 50))
        started = time.monotonic()
        state = State(query, kind, full, limit, where, args,
                      None if budget_ms is None else started + budget_ms / 1000)
        for stage in self.stages:
            begun = time.monotonic()
            try:
                stage.run(state)
                outcome = "ok"
            except Exception as e:  # noqa: BLE001 - an optional stage must never cost the search
                if stage.fallback is None:
                    raise
                state.degraded.append(stage.fallback.format(e))
                outcome = "failed"
            state.trace.append({"stage": stage.name, "ms": round((time.monotonic() - begun) * 1000, 1),
                                "outcome": outcome})
        self._log(state)
        reply = state.reply
        if state.degraded:
            reply["degraded"] = state.degraded
        if trace:
            reply["trace"] = state.trace
        return reply

    def request(self, body) -> dict:
        """A `POST /v1/search` body. A ValueError is the caller's mistake."""
        if not isinstance(body, dict):
            raise ValueError("the body must be a JSON object")
        if extra := set(body) - REQUEST_FIELDS:
            raise ValueError(f"unknown fields: {sorted(extra)}")
        if not isinstance(body.get("query"), str) or not body["query"].strip():
            raise ValueError("needs `query`")
        for name in ("limit", "budget_ms"):
            if body.get(name) is not None and (type(body[name]) is not int or body[name] < 1):
                raise ValueError(f"`{name}` must be a positive whole number")
        return self.run(**body)

    # --- stages ---------------------------------------------------------------------

    def _filter(self, s: State) -> None:
        s.allowed = {r["id"] for r in self.db.execute(f"SELECT r.id FROM records r WHERE {s.where}", s.args)}

    def _keywords(self, s: State) -> None:
        index = self.keyword_index()
        if index is None:
            s.degraded.append("keyword index is not built yet")
        else:
            s.keyword_ids = [i for i in index.search(s.query, 1000) if i in s.allowed][:200]

    def _vectors(self, s: State) -> None:
        if not ix.EMBED_URL:
            raise RuntimeError("no embedding provider configured")
        # Short timeout: keyword-only with `degraded` beats a hung call.
        qv = np.asarray(ix.embed([s.query], timeout=s.left(QUERY_EMBED_TIMEOUT))[0], dtype=np.float32)
        qv /= np.linalg.norm(qv) or 1.0
        hits = list(self.db.execute(
            f"SELECT r.id, 1 - (r.embedding <=> %s) AS cosine FROM records r WHERE {s.where} "
            "AND r.embedding IS NOT NULL AND r.embed_model = %s "
            "ORDER BY r.embedding <=> %s LIMIT 200", [qv, *s.args, ix.EMBED_MODEL, qv]))
        s.vector_ids = [r["id"] for r in hits]
        s.cosine = {r["id"]: float(r["cosine"]) for r in hits}
        s.unembedded = {r["id"] for r in self.db.execute(
            "SELECT id FROM records WHERE embedding IS NULL OR embed_model IS DISTINCT FROM %s", (ix.EMBED_MODEL,))}
        if not s.vector_ids and s.allowed:
            raise RuntimeError("no embeddings stored")

    def _fuse(self, s: State) -> None:
        s.ranks = ranking.fuse(s.keyword_ids, s.vector_ids, unembedded=s.unembedded)
        s.ordered = list(s.ranks)

    def _rerank(self, s: State) -> None:
        """Rerank the best candidates that are not tools, which rank in their own list."""
        if not ranking.RERANK_MODEL:
            return
        head = [r for r in s.ordered if not r.startswith("tool:")][:ranking.RERANK_CANDIDATES]
        if len(head) < 2:
            return
        rows = {r["id"]: r for r in self.db.execute(
            "SELECT id, name, description FROM records WHERE id = ANY(%s)", (head,))}
        best = ranking.rerank(s.query, [rows[r] for r in head], timeout=s.left(ranking.RERANK_TIMEOUT))
        chosen = set(best)
        s.ordered = best + [r for r in s.ordered if r not in chosen]

    def _shape(self, s: State) -> None:
        if s.kind:
            top, tool_ids = s.ordered[:s.limit], []
        else:
            # Tools rank in their own list so a large tool set cannot crowd skills out.
            top = [r for r in s.ordered if not r.startswith("tool:")][:s.limit]
            tool_ids = [r for r in s.ordered if r.startswith("tool:")
                        and (not s.cosine or s.cosine.get(r, 0) >= TOOL_MIN_COSINE)][:TOOL_HITS]
        want = top + tool_ids
        rows = {r["id"]: r for r in self.db.execute(
            "SELECT * FROM records WHERE id = ANY(%s)", (want,))} if want else {}

        def item(rid: str) -> dict:
            r = rows[rid]
            if s.full:
                d = summary(r) | {"score": round(s.ranks.get(rid, 0) * 60, 3)}
            else:
                # The id already encodes kind, plugin and name; the score is only an ordering.
                desc = " ".join((r["description"] or "").split())
                d = {"id": rid, "description": desc if len(desc) <= SEARCH_DESC_CHARS
                     else desc[:SEARCH_DESC_CHARS - 1] + "…"}
            if r["kind"] == "tool":
                # "Listed natively" is about the main client group; a scoped user calls through a toolbox.
                native = not SCOPED and r["body"].startswith("call: listed natively")
                d["call"] = f"mcp__{ix.CLIENT_NAME}__{r['name']}" if native else "toolbox__call_tool"
            return d

        out = {"results": [item(rid) for rid in top]}
        if tool_ids:
            out["tools"] = [item(rid) for rid in tool_ids]
        scored = [s.cosine[rid] for rid in top if rid in s.cosine]
        if scored:   # absent when search is keyword only: there is nothing to measure
            out["confidence"] = round(max(scored), 3)
            if out["confidence"] < CONFIDENCE_MIN:
                out["weak"] = True
        if not top and not self.db.execute("SELECT 1 FROM records LIMIT 1").fetchone():
            out["error"] = "the index is EMPTY - this is a fault, not a miss. Run health."
        s.reply = out

    def _log(self, s: State) -> None:
        """What was asked and shown, so a real miss can be found later. Never fails a search."""
        if LOG_DAYS <= 0:
            return
        shown = [r["id"] for r in s.reply.get("results", []) + s.reply.get("tools", [])]
        try:
            self.db.execute("INSERT INTO system.search_log (query, results, degraded) VALUES (%s, %s, %s)",
                            (s.query[:LOG_QUERY_CHARS], shown, bool(s.degraded)))
            if time.monotonic() - self.pruned > 86400 or not self.pruned:
                self.pruned = time.monotonic()
                self.db.execute("DELETE FROM system.search_log WHERE at < now() - make_interval(days => %s)",
                                (LOG_DAYS,))
        except Exception as e:  # noqa: BLE001
            ix.log(f"search log failed: {e!r}")
