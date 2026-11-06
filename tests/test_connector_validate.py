import importlib.util
import json
from pathlib import Path

path = Path(__file__).parent.parent / "plugins/canopy-connector/skills/validate/scripts/validate.py"
spec = importlib.util.spec_from_file_location("validate", path)
validate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate)


def test_a_failure_is_red_however_fast():
    assert validate.verdict(["x"], 1).startswith("RED")


def test_slow_is_amber_and_fast_is_green():
    assert validate.verdict([], validate.SLOW_MS + 1).startswith("AMBER")
    assert validate.verdict([], 10).startswith("GREEN")


def test_green_claims_retrieval_only_when_it_was_probed():
    assert "retrieval" in validate.verdict([], 10)
    assert "retrieval" not in validate.verdict([], 10, probed=False)


def test_parse_reads_plain_json_and_events():
    assert validate.parse('{"id": 1}') == {"id": 1}
    assert validate.parse('event: message\ndata: {"id": 2}\n') == {"id": 2}


def test_a_miss_or_keyword_fallback_is_amber():
    assert validate.verdict([], 10, missed=1).startswith("AMBER")
    assert validate.verdict([], 10, degraded=2).startswith("AMBER")
    assert validate.verdict(["x"], 10, missed=1).startswith("RED")


def test_phrase_takes_the_first_sentence_and_falls_back_to_the_name():
    assert validate.phrase({"description": "Resize photos. Use on x.", "name": "n"}) == "Resize photos."
    assert len(validate.phrase({"description": "word " * 40, "name": "n"}).split()) == validate.QUERY_WORDS
    assert validate.phrase({"description": "", "name": "resize-photos"}) == "resize photos"


class FakeClient:
    def __init__(self, ids, total=100):
        self.ids, self.total, self.offsets = ids, total, []

    def tool(self, name, arguments):
        if name == "catalogue__list":
            self.offsets.append(arguments.get("offset"))
            return json.dumps({"total": self.total, "records": [{"id": "skill:p/s", "name": "s", "description": "Do it."}]})
        if name == "catalogue__search":
            return json.dumps({"results": [{"id": i} for i in self.ids if i.startswith("skill:")],
                               "tools": [{"id": i} for i in self.ids if i.startswith("tool:")]})
        return "x" * 400


def test_sample_spreads_through_the_catalogue_and_copes_with_few_or_none():
    client = FakeClient([])
    assert len(validate.sample(client, 5)) == 5
    assert client.offsets[1:] == [10, 30, 50, 70, 90]
    assert len(validate.sample(FakeClient([], total=2), 5)) == 2
    assert validate.sample(FakeClient([], total=0), 5) == []


def test_probe_finds_tools_and_skills_and_counts_the_load():
    hit = validate.probe(FakeClient(["skill:a/b", "tool:x"]), {"q": "q", "relevant": ["tool:x"]}, None)
    assert hit["rank"] == 1 and hit["read_tokens"] == 100
    miss = validate.probe(FakeClient(["skill:a/b"]), {"q": "q", "relevant": ["skill:c/d"]}, None)
    assert miss["rank"] is None and miss["read_tokens"] is None


def test_summarise_counts_misses_and_survives_no_rows():
    rows = [{"rank": 1, "ms": 10, "reply_tokens": 5, "read_tokens": 5, "degraded": False},
            {"rank": None, "ms": 30, "reply_tokens": 7, "read_tokens": None, "degraded": True}]
    summary = validate.summarise(rows, 50)
    assert (summary["found"], summary["first"], summary["mrr"], summary["degraded"]) == (1, 1, 0.5, 1)
    assert summary["search_plus_read_tokens_p50"] == 10
    assert validate.summarise([], 50)["probes"] == 0


def test_compare_warns_when_the_queries_differ():
    run = {"summary": {"found": 1}, "rows": [{"query": "a"}]}
    assert not any("differ" in line for line in validate.compare(run, run))
    assert any("differ" in line for line in validate.compare(run, {"summary": {"found": 1}, "rows": [{"query": "b"}]}))
