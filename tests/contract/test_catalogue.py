"""The catalogue on the real database: storage, vectors and search, with a stand-in embedder."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import types
from pathlib import Path

import numpy as np
import pytest

from canopy import catalogue as cat
from canopy import import_vectors as iv
from canopy import index as ix
from canopy import ranking
from canopy import retrieval

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
    cat._keywords.update(index=None, hashes={})


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
    assert out["confidence"] > 0.99 and "weak" not in out


def test_a_poor_match_says_it_is_weak(embedder, monkeypatch):
    load_fixture()
    ix.embed_pending(cat.db)
    monkeypatch.setattr(retrieval, "CONFIDENCE_MIN", 1.01)
    out = search(ix.embed_text(dict(stored("skill:media/video-trim"))))
    assert out["weak"] is True and out["results"]   # results stay: weak is a signal, not a filter


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


def test_a_new_skill_updates_the_keyword_index_in_place(monkeypatch):
    monkeypatch.setattr(ix, "EMBED_URL", "")
    load_fixture()
    index = cat._keywords["index"]
    before = len(index)
    written = []
    update = index.update
    monkeypatch.setattr(index, "update", lambda rows=(), removed=(): (written.append(list(rows)), update(rows, removed)))
    cat.refresh_keywords()
    assert written == []                       # nothing changed, nothing touched
    store(rec("skill:p/new", description="Polish the zeppelin"))
    cat.refresh_keywords()
    assert cat._keywords["index"] is index     # never a rebuild
    assert [[r["id"] for r in rows] for rows in written] == [["skill:p/new"]]
    assert len(index) == before + 1
    assert search("polish zeppelin")["results"][0]["id"] == "skill:p/new"


def test_a_removed_record_leaves_the_keyword_index(monkeypatch):
    monkeypatch.setattr(ix, "EMBED_URL", "")
    load_fixture()
    store(rec("skill:p/gone", description="Polish the zeppelin"))
    cat.refresh_keywords()
    cat.db.execute("DELETE FROM records WHERE id = 'skill:p/gone'")
    cat.refresh_keywords()
    assert cat._keywords["index"].search("zeppelin") == []


def test_a_search_is_logged_and_a_log_failure_costs_nothing(monkeypatch):
    monkeypatch.setattr(ix, "EMBED_URL", "")
    load_fixture()
    cat.db.execute("TRUNCATE system.search_log")
    out = search("merge pdf files")
    row = cat.db.execute("SELECT query, results, degraded FROM system.search_log").fetchone()
    assert row["query"] == "merge pdf files" and row["degraded"] is True
    assert row["results"] == [r["id"] for r in out["results"]]
    monkeypatch.setattr(retrieval, "LOG_DAYS", 0)
    search("merge pdf files")
    assert cat.db.execute("SELECT COUNT(*) AS n FROM system.search_log").fetchone()["n"] == 1


def test_the_trace_names_every_stage(monkeypatch):
    monkeypatch.setattr(ix, "EMBED_URL", "")
    load_fixture()
    trace = cat.searcher.run("merge pdf files", trace=True)["trace"]
    assert [t["stage"] for t in trace] == ["filter", "keywords", "vectors", "fuse", "rerank", "shape"]
    assert {t["stage"]: t["outcome"] for t in trace}["vectors"] == "failed"
    assert "trace" not in cat.searcher.run("merge pdf files")


def test_a_spent_budget_skips_the_slow_stages_and_still_answers(embedder, monkeypatch):
    load_fixture()
    monkeypatch.setattr(ranking, "RERANK_MODEL", "m")
    monkeypatch.setattr(ranking, "rerank", lambda *a, **k: pytest.fail("the budget was spent"))
    calls = len(embedder)
    clock = iter(range(0, 10000, 100))   # this module only: the pool's threads share the real `time`
    monkeypatch.setattr(retrieval, "time", types.SimpleNamespace(monotonic=lambda: next(clock)))
    out = cat.searcher.run("merge pdf files", budget_ms=1)   # the clock passes the deadline at once
    assert out["results"][0]["id"] == "skill:docs/pdf-merge"
    assert len(embedder) == calls
    assert sum("out of budget" in d for d in out["degraded"]) == 2


def test_every_tool_is_registered():
    names = {t.name for t in asyncio.run(cat.mcp.list_tools())}
    assert {"search", "read", "list", "mcp_servers", "health", "usage", "sync", "embed"} <= names


def test_the_rest_body_is_checked():
    for body in ([], {}, {"query": " "}, {"query": "x", "nope": 1}, {"query": "x", "limit": 0},
                 {"query": "x", "budget_ms": "5"}, {"query": "x", "kind": "nope"}):
        with pytest.raises(ValueError):
            cat.searcher.request(body)


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
    monkeypatch.setattr(ranking, "rerank", lambda q, cands, **k: [c["id"] for c in reversed(cands)])
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


# --- import from an older SQLite index ------------------------------------------

def key_of(r: dict) -> str:
    return hashlib.sha256(ix.embed_text(r).encode()).hexdigest()


def old_index(path, *rows: tuple[str, str, bytes]) -> Path:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE embeddings (key TEXT PRIMARY KEY, model TEXT, vec BLOB)")
    db.executemany("INSERT INTO embeddings VALUES (?,?,?)", rows)
    db.commit()
    db.close()
    return path


def blob(text: str, scale: float = 1.0) -> bytes:
    return (np.asarray(fake_vector(text), dtype=np.float32) * scale).tobytes()


def test_import_lands_a_vector_on_every_record_sharing_its_text(tmp_path):
    a = rec("skill:p/a")
    twin = rec("skill:p/b", name="a")
    store(a, twin)
    assert key_of(a) == stored("skill:p/a")["embed_key"] == stored("skill:p/b")["embed_key"]
    report = iv.import_vectors(cat.db, old_index(tmp_path / "old.db", (key_of(a), "bge-m3", blob("a", 7.0))))
    assert report["records_filled"] == 2 and report["still_pending"] == 0
    got = np.asarray(stored("skill:p/a")["embedding"].to_list())
    want = np.frombuffer(blob("a"), dtype=np.float32)
    assert np.allclose(got, want / np.linalg.norm(want), atol=1e-5)   # stored as a unit vector
    assert stored("skill:p/a")["embed_model"] == "bge-m3"
    assert ix.pending_estimate(cat.db)["records"] == 0


def test_import_accepts_a_providers_name_for_the_model_and_stores_the_canonical_one(tmp_path):
    a = rec("skill:p/a")
    store(a)
    report = iv.import_vectors(cat.db, old_index(tmp_path / "old.db", (key_of(a), "baai/bge-m3", blob("a"))))
    assert report["records_filled"] == 1
    assert stored("skill:p/a")["embed_model"] == "bge-m3"


def test_import_skips_what_it_cannot_use_and_leaves_those_records_pending(tmp_path):
    a, b, c = rec("skill:p/a"), rec("skill:p/b", description="B"), rec("skill:p/c", description="C")
    store(a, b, c)
    report = iv.import_vectors(cat.db, old_index(
        tmp_path / "old.db",
        (key_of(a), "text-embedding-3-small", blob("a")),
        (key_of(b), "bge-m3", b"short"),
        (key_of(c), "bge-m3", bytes(1024 * 4)),   # all zeros: no direction to keep
        ("gone", "bge-m3", blob("gone"))))
    assert report == {"source_vectors": 4, "other_model": 1, "unusable": 2, "no_record": 1, "kept": 0,
                      "records_filled": 0, "still_pending": 3}
    assert stored("skill:p/a")["embedding"] is None


def test_import_does_not_overwrite_a_current_vector_and_can_be_run_again(tmp_path, embedder):
    a = rec("skill:p/a")
    store(a)
    ix.embed_pending(cat.db)
    before = stored("skill:p/a")["embedding"].to_list()
    old = old_index(tmp_path / "old.db", (key_of(a), "bge-m3", blob("different")))
    assert iv.import_vectors(cat.db, old)["kept"] == 1
    assert iv.import_vectors(cat.db, old)["records_filled"] == 0
    assert stored("skill:p/a")["embedding"].to_list() == before


def test_import_replaces_a_vector_from_another_model(tmp_path, embedder, monkeypatch):
    a = rec("skill:p/a")
    store(a)
    monkeypatch.setattr(ix, "EMBED_MODEL", "other")
    ix.embed_pending(cat.db)
    report = iv.import_vectors(cat.db, old_index(tmp_path / "old.db", (key_of(a), "bge-m3", blob("a"))))
    assert report["records_filled"] == 1
    assert stored("skill:p/a")["embed_model"] == "bge-m3"


def test_import_before_a_sync_is_refused(tmp_path):
    old = old_index(tmp_path / "old.db", ("k", "bge-m3", blob("a")))
    with pytest.raises(ValueError, match="full sync"):
        iv.import_vectors(cat.db, old)


def test_import_of_a_missing_file_is_refused_and_creates_nothing(tmp_path):
    store(rec("skill:p/a"))
    with pytest.raises(ValueError, match="no such file"):
        iv.import_vectors(cat.db, tmp_path / "typo.db")
    assert not (tmp_path / "typo.db").exists()


def test_no_tool_runs_on_the_event_loop():
    """FastMCP calls a plain function inline, so one slow tool held up every other."""
    assert all(t.is_async for t in cat.mcp._tool_manager.list_tools())


def test_the_disk_count_is_kept_until_the_clone_moves(tmp_path, monkeypatch):
    clone = tmp_path / "clones" / "p"
    (clone / ".git").mkdir(parents=True)
    (clone / ".git" / "FETCH_HEAD").write_text("aaa")
    monkeypatch.setattr(ix, "clones", lambda: tmp_path / "clones")
    calls = []
    monkeypatch.setattr(ix, "extract", lambda plugin, root: calls.append(root) or [{}, {}])
    ix._disk_counts.clear()
    assert [ix.records_on_disk("p", clone) for _ in range(2)] == [3, 3] and len(calls) == 1
    (clone / ".git" / "FETCH_HEAD").write_text("bbb")
    assert ix.records_on_disk("p", clone) == 3 and len(calls) == 2

