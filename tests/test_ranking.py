import json
from pathlib import Path

import httpx
import pytest

from canopy import ranking

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "search.json").read_text())


def doc(rid, **fields):
    return {"id": rid, "name": "", "description": "", "plugin": "", "body": "", **fields}


def test_stemming_matches_word_forms():
    index = ranking.KeywordIndex([doc("a", description="Transcribing recordings"), doc("b", description="Other")])
    assert index.search("transcribed recording") == ["a"]


def test_a_name_outranks_the_same_word_in_a_body():
    filler = "and some more words about other things " * 3
    index = ranking.KeywordIndex([doc("body", body=f"deploy {filler}"), doc("name", name="deploy", body=filler)])
    assert index.search("deploy")[0] == "name"


def test_hyphenated_names_match_their_parts():
    assert ranking.KeywordIndex([doc("a", name="pdf-merge")]).search("merge") == ["a"]


def test_no_match_and_an_empty_index_return_nothing():
    assert ranking.KeywordIndex([doc("a", name="x1")]).search("zebra") == []
    assert ranking.KeywordIndex([]).search("zebra") == []


def test_update_replaces_adds_and_removes_in_place():
    index = ranking.KeywordIndex([doc("a", description="zebra"), doc("b", description="yak")])
    index.update([doc("a", description="walrus"), doc("c", description="zebra")], removed=["b"])
    assert len(index) == 2
    assert index.search("zebra") == ["c"]
    assert index.search("walrus") == ["a"]
    assert index.search("yak") == []


def test_a_query_cannot_reach_the_query_language():
    index = ranking.KeywordIndex([doc("a", name="zebra")])
    for query in ('zebra AND NOT', 'name:zebra', '"zebra', 'zebra^9 (', 'body:* OR zebra', 'zebra IN [a b]'):
        assert index.search(query) == ["a"], query
    assert index.search("?! -") == []


def test_hebrew_is_searchable():
    assert ranking.KeywordIndex([doc("a", description="שליחת דואר")]).search("דואר") == ["a"]


def test_vectors_outweigh_keywords_three_to_one():
    fused = ranking.fuse(["k", "both"], ["v", "both"], vector_weight=3)
    assert list(fused) == ["both", "v", "k"]


def test_keywords_still_add_what_vectors_missed():
    assert "exact-name" in ranking.fuse(["exact-name"], ["v1", "v2"])


def reply(body, status=200):
    return lambda url, **kw: httpx.Response(status, json=body, request=httpx.Request("POST", url))


CANDIDATES = [{"id": "a", "name": "a", "description": "x"}, {"id": "b", "name": "b", "description": None}]


def test_rerank_returns_the_new_order():
    post = reply({"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]})
    assert ranking.rerank("q", CANDIDATES, post=post) == ["b", "a"]


def test_rerank_raises_on_a_partial_answer_or_an_error():
    with pytest.raises(RuntimeError):
        ranking.rerank("q", CANDIDATES, post=reply({"results": [{"index": 1}]}))
    with pytest.raises(httpx.HTTPError):
        ranking.rerank("q", CANDIDATES, post=reply({}, 500))


def test_keyword_regression_on_the_labelled_set():
    """Keywords alone, on a small generic set whose queries mostly share words with their skill.
    A floor, so a broken tokeniser or scorer shows here. It says nothing about real-catalogue
    quality: `scripts/eval-search.py` measures that. Scored 28 first of 29 on 2026-11-05."""
    index = ranking.KeywordIndex(FIXTURE["records"])
    ranks = []
    for q in FIXTURE["queries"]:
        found = index.search(q["query"], 6)
        ranks.append(found.index(q["expected"][0]) + 1 if q["expected"][0] in found else None)
    first = sum(r == 1 for r in ranks)
    top3 = sum(r is not None and r <= 3 for r in ranks)
    assert (first, top3) >= (FLOOR_FIRST, FLOOR_TOP3)


FLOOR_FIRST, FLOOR_TOP3 = 27, 28


def test_a_record_with_no_vector_is_not_buried_by_the_vector_weight():
    vectors = [f"v{i}" for i in range(200)]
    assert list(ranking.fuse(["new"], vectors, vector_weight=3, unembedded={"new"}))[0] == "new"
    assert list(ranking.fuse(["new"], vectors, vector_weight=3)).index("new") > 100
