"""The catalogue on the real database: storage, vectors and search, with a stand-in embedder."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from canopy import catalogue as cat
from canopy import index as ix
from canopy import ranking

FIXTURE = json.loads((Path(__file__).parent.parent / "fixtures" / "search.json").read_text())


def rec(rid: str, plugin: str = "p", description: str = "A thing", name: str | None = None) -> dict:
    return {"id": rid, "kind": "skill", "plugin": plugin, "name": name or rid.rsplit("/", 1)[-1],
            "description": description, "path": "", "body": "Body", "hash": hashlib.sha256(description.encode()).hexdigest()}


def fake_vector(text: str) -> list[float]:
    seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
    return np.random.default_rng(seed).normal(size=1024).tolist()


@pytest.fixture
def embedder(monkeypatch):
    calls = []

    def embed(texts, timeout=120):
        calls.append(texts)
        return [fake_vector(t) for t in texts]
    monkeypatch.setattr(ix, "EMBED_URL", "http://stand-in/v1")
    monkeypatch.setattr(ix, "embed", embed)
    return calls


@pytest.fixture(autouse=True)
def clean():
    cat.db.execute("TRUNCATE records, plugins, reads, runs")
    cat._keywords.update(index=None, signature=None)


def stored(rid: str):
    return cat.db.execute("SELECT * FROM records WHERE id = %s", (rid,)).fetchone()


def store(*recs: dict, plugin: str = "p") -> None:
    with cat.db:
        ix._store(cat.db, plugin, list(recs))


def test_a_vector_survives_a_resync_of_the_same_text(embedder):
    store(rec("skill:p/a"))
    assert ix.embed_pending(cat.db) == (1, None)
    store(rec("skill:p/a"))
    assert stored("skill:p/a")["embedding"] is not None
    assert ix.embed_pending(cat.db) == (0, None)


def test_changed_text_loses_its_vector_and_goes_back_to_pending(embedder):
    store(rec("skill:p/a"))
    ix.embed_pending(cat.db)
    store(rec("skill:p/a", description="Rewritten"))
    assert stored("skill:p/a")["embedding"] is None
    assert ix.pending_estimate(cat.db)["records"] == 1


def test_a_vector_from_another_model_counts_as_pending(embedder, monkeypatch):
    store(rec("skill:p/a"))
    ix.embed_pending(cat.db)
    monkeypatch.setattr(ix, "EMBED_MODEL", "other")
    assert ix.pending_estimate(cat.db)["records"] == 1


def test_records_with_one_text_are_embedded_once(embedder):
    twin = {**rec("skill:p/b", name="a")}
    one = rec("skill:p/a")
    twin["id"] = "skill:p/a2"
    store(one, twin)
    assert ix.embed_pending(cat.db) == (1, None)
    assert stored("skill:p/a2")["embedding"] is not None


def test_a_plugin_store_removes_what_it_no_longer_has():
    store(rec("skill:p/a"), rec("skill:p/b"))
    store(rec("skill:p/b"))
    assert stored("skill:p/a") is None and stored("skill:p/b")


def test_a_store_leaves_other_plugins_alone():
    store(rec("skill:q/a", plugin="q"), plugin="q")
    store(rec("skill:p/a"))
    assert stored("skill:q/a")


def test_a_failed_transaction_leaves_nothing_behind():
    store(rec("skill:p/a"))
    with pytest.raises(RuntimeError):
        with cat.db:
            ix._store(cat.db, "p", [])
            raise RuntimeError("crash mid-sync")
    assert stored("skill:p/a")


def test_a_nul_byte_in_a_skill_does_not_fail_the_plugin():
    store({**rec("skill:p/a"), "body": "bad\x00byte"})
    assert stored("skill:p/a")["body"] == "badbyte"


def load_fixture() -> None:
    by_plugin: dict[str, list] = {}
    for r in FIXTURE["records"]:
        by_plugin.setdefault(r["plugin"], []).append(
            {**r, "path": "", "hash": hashlib.sha256(r["description"].encode()).hexdigest()})
    for plugin, recs in by_plugin.items():
        store(*recs, plugin=plugin)
    cat.refresh_keywords()


def search(*args, **kw) -> dict:
    return json.loads(cat.search(*args, **kw))


def test_keyword_search_says_it_is_keyword_only(monkeypatch):
    monkeypatch.setattr(ix, "EMBED_URL", "")
    load_fixture()
    out = search("merge pdf files")
    assert out["results"][0]["id"] == "skill:docs/pdf-merge"
    assert "keyword only" in out["degraded"][0]


def test_hybrid_search_uses_the_stored_vectors(embedder):
    load_fixture()
    ix.embed_pending(cat.db)
    wanted = stored("skill:media/video-trim")
    text = ix.embed_text(dict(wanted))
    out = search(text)   # the same text, so the same stand-in vector: distance 0
    assert out["results"][0]["id"] == "skill:media/video-trim" and "degraded" not in out


def test_a_broken_embedder_degrades_to_keywords(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("embedder down")
    monkeypatch.setattr(ix, "EMBED_URL", "http://stand-in/v1")
    monkeypatch.setattr(ix, "embed", broken)
    load_fixture()
    out = search("merge pdf files")
    assert out["results"] and "embedder down" in out["degraded"][0]


def test_filters_apply_to_both_rankings(embedder):
    load_fixture()
    ix.embed_pending(cat.db)
    out = search("merge pdf files", plugin="media")
    assert out["results"] and all("/" in r["id"] and ":media/" in r["id"] for r in out["results"])


def test_an_empty_index_is_a_fault_not_a_miss(monkeypatch):
    monkeypatch.setattr(ix, "EMBED_URL", "")
    cat.refresh_keywords()
    assert "EMPTY" in search("anything")["error"]


def test_keywords_rebuild_only_when_records_change(monkeypatch):
    monkeypatch.setattr(ix, "EMBED_URL", "")
    load_fixture()
    first = cat._keywords["index"]
    cat.refresh_keywords()
    assert cat._keywords["index"] is first
    store(rec("skill:p/new", description="Something new"))
    cat.refresh_keywords()
    assert cat._keywords["index"] is not first


def test_a_failed_rerank_keeps_the_fused_order(monkeypatch):
    monkeypatch.setattr(ix, "EMBED_URL", "")
    load_fixture()
    plain = [r["id"] for r in search("merge pdf files text")["results"]]
    monkeypatch.setattr(ranking, "RERANK_MODEL", "m")

    def broken(*a, **k):
        raise RuntimeError("reranker down")
    monkeypatch.setattr(ranking, "rerank", broken)
    out = search("merge pdf files text")
    assert [r["id"] for r in out["results"]] == plain
    assert any("reranker down" in d for d in out["degraded"])


def test_a_rerank_decides_the_order(monkeypatch):
    monkeypatch.setattr(ix, "EMBED_URL", "")
    load_fixture()
    monkeypatch.setattr(ranking, "RERANK_MODEL", "m")
    monkeypatch.setattr(ranking, "rerank", lambda q, cands: [c["id"] for c in reversed(cands)])
    plain = cat._keywords["index"].search("merge pdf files text", 20)
    assert search("merge pdf files text")["results"][0]["id"] == plain[-1]


def test_read_list_usage_servers_and_health_run_on_postgres(monkeypatch):
    monkeypatch.setattr(ix, "EMBED_URL", "")
    load_fixture()
    assert "Merge several PDF" in cat.read(["skill:docs/pdf-merge"])
    assert "no such record" in cat.read(["skill:docs/nosuch"])
    assert json.loads(cat.list_records(kind="skill", limit=5))["total"] == len(FIXTURE["records"])
    used = json.loads(cat.usage())
    assert [r["id"] for r in used["records"]] == ["skill:docs/pdf-merge"] and used["records"][0]["reads"] == 1
    assert json.loads(cat.usage(kind="file"))["total"] == 0
    assert json.loads(cat.usage(plugin="docs"))["total"] == 1
    json.loads(cat.mcp_servers())
    assert json.loads(cat.health())["counts"]["skill"] == len(FIXTURE["records"])


def test_gateway_snapshot_and_mcp_records(g, uid):
    from conftest import server
    g.register(server(f"s{uid}"))
    assert f"s{uid}" in ix.refresh_gateway(cat.db)
    ix.refresh_tools(cat.db)
    assert ix.rebuild_mcp_records(cat.db) >= 3
    assert stored(f"mcp:s{uid}") and stored(f"tool:s{uid}__s{uid}_a")
    assert stored(f"tool:s{uid}__s{uid}_a")["kind"] == "tool"
