import httpx
import pytest
from starlette.testclient import TestClient

from canopy import api
from canopy.embedder import Embedder, EmbedError
from test_api import AUTH, Stub


def fake(answers):
    """`answers` maps a URL fragment to a vector count or an exception."""
    calls = []

    def post(url, **kw):
        calls.append((url, kw["json"]["model"], kw["timeout"]))
        for part, answer in answers.items():
            if part in url:
                if isinstance(answer, Exception):
                    raise answer
                return httpx.Response(200, json={"data": [{"index": i, "embedding": [0.0]} for i in range(answer)]},
                                      request=httpx.Request("POST", url))
        raise AssertionError(url)
    return post, calls


def test_the_provider_gets_its_own_name_and_the_reply_keeps_the_canonical_one():
    post, calls = fake({"openrouter": 1})
    e = Embedder("openrouter", "", provider_model=lambda m, p: "baai/bge-m3", post=post)
    reply = e.embed("bge-m3", ["x"])
    assert calls[0][1] == "baai/bge-m3" and reply["model"] == "bge-m3" and reply["route"] == "openrouter"


def test_a_slow_primary_falls_back():
    post, calls = fake({"ollama": httpx.ReadTimeout("slow"), "openrouter": 1})
    assert Embedder("local", "openrouter", post=post).embed("m", ["x"])["route"] == "openrouter"
    assert len(calls) == 2


def test_a_query_gets_the_short_budget_and_a_batch_does_not():
    post, calls = fake({"ollama": 2})
    e = Embedder("local", "", post=post)
    e.embed("m", ["a", "b"])
    post, query_calls = fake({"ollama": 1})
    Embedder("local", "", post=post).embed("m", ["a"])
    assert query_calls[0][2] < calls[0][2]


def test_a_short_answer_counts_as_a_failure():
    post, _ = fake({"ollama": 1})
    with pytest.raises(EmbedError) as e:
        Embedder("local", "", post=post).embed("m", ["a", "b"])
    assert e.value.status == 502


def test_no_provider_is_a_clear_503():
    with pytest.raises(EmbedError) as e:
        Embedder("", "").embed("m", ["a"])
    assert e.value.status == 503


def test_an_unknown_provider_is_refused_at_start():
    with pytest.raises(ValueError):
        Embedder("nosuch", "")


def test_the_route_needs_the_token_and_passes_failures_on():
    client = TestClient(api.create_app(Stub(), "right"))
    assert client.post("/v1/embeddings", json={"model": "m", "input": "x"}).status_code == 401
    assert client.post("/v1/embeddings", json={"model": "m", "input": "x"}, headers=AUTH).status_code == 503
    assert client.post("/v1/embeddings", json={"input": "x"}, headers=AUTH).status_code == 400


def test_the_route_answers_with_vectors():
    post, _ = fake({"ollama": 1})
    client = TestClient(api.create_app(Stub(), "right", Embedder("local", "", post=post)))
    r = client.post("/v1/embeddings", json={"model": "m", "input": "x"}, headers=AUTH)
    assert r.status_code == 200 and len(r.json()["data"]) == 1


def _reply(calls):
    def post(url, json, **_):
        calls.append(url)
        class R:
            def raise_for_status(self): pass
            def json(self): return {"data": [{"embedding": [0.0]} for _ in json["input"]]}
        return R()
    return post


def test_a_query_takes_its_own_route_and_bulk_keeps_the_primary():
    calls = []
    e = Embedder("openrouter", "local", query_first="local", post=_reply(calls))
    assert e.embed("m", ["one query"])["route"] == "local"
    assert e.embed("m", ["a", "b"])["route"] == "openrouter"


def test_a_query_route_that_fails_falls_through_to_the_other():
    def post(url, json, **_):
        if "openrouter" not in url:
            raise httpx.ConnectError("down")
        return _reply([])(url, json)
    e = Embedder("openrouter", "local", query_first="local", post=post)
    assert e.embed("m", ["one query"])["route"] == "openrouter"
