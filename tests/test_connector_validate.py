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
    def __init__(self, ids, total=100, deep=None, absent=()):
        self.ids, self.total, self.offsets, self.deep, self.absent = ids, total, [], deep, absent

    def tool(self, name, arguments):
        if name == "catalogue__list":
            self.offsets.append(arguments.get("offset"))
            return json.dumps({"total": self.total, "records": [{"id": "skill:p/s", "name": "s", "description": "Do it."}]})
        if name == "catalogue__search":
            ids = self.deep if self.deep and arguments.get("limit") == validate.DEEP_LIMIT else self.ids
            return json.dumps({"results": [{"id": i} for i in ids if i.startswith(("skill:", "plugin:"))],
                               "tools": [{"id": i} for i in ids if i.startswith("tool:")]})
        if arguments["ids"][0] in self.absent:
            return f'=== {arguments["ids"][0]}\n{{"error":"no such record; use list or search to find the id"}}'
        return "=== id\n" + "x" * 400


def test_sample_spreads_through_the_catalogue_and_copes_with_few_or_none():
    client = FakeClient([])
    assert len(validate.sample(client, 5)) == 5
    assert client.offsets[1:] == [10, 30, 50, 70, 90]
    assert len(validate.sample(FakeClient([], total=2), 5)) == 2
    assert validate.sample(FakeClient([], total=0), 5) == []


def test_probe_finds_tools_and_skills_and_counts_the_load():
    hit = validate.probe(FakeClient(["skill:a/b", "tool:x"]), {"q": "q", "relevant": ["tool:x"]}, None)
    assert hit["rank"] == 1 and hit["read_tokens"] == 101
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


KNOWN = {"q": "q", "relevant": ["skill:a/right"], "close": ["skill:a/sibling"]}


def known(ids, **fake):
    return validate.probe(FakeClient(ids, **fake), KNOWN, None, known=True)


def test_a_known_skill_on_top_is_first():
    assert known(["skill:a/right", "skill:z/other"])["grade"] == "first"


def test_its_own_plugin_and_listed_close_matches_do_not_count_against_it():
    row = known(["plugin:a", "skill:a/sibling", "skill:a/right"])
    assert (row["rank"], row["grade"], row["unexpected"]) == (3, "behind-close", [])


def test_anything_else_above_it_is_named_for_judging():
    row = known(["skill:a/sibling", "skill:z/other", "skill:a/right"])
    assert row["grade"] == "outranked" and row["unexpected"] == ["skill:z/other"]


def test_a_miss_says_whether_it_is_buried_lost_or_not_indexed():
    buried = known(["skill:z/other"], deep=["skill:z/other", "skill:a/right"])
    assert (buried["rank"], buried["grade"], buried["deep_rank"]) == (None, "buried", 2)
    assert known(["skill:z/other"])["grade"] == "missed"
    assert known(["skill:z/other"], absent=["skill:a/right"])["grade"] == "not-indexed"


def test_sampled_probes_are_not_graded():
    assert "grade" not in validate.probe(FakeClient(["skill:z/other", "skill:a/b"]), {"q": "q", "relevant": ["skill:a/b"]}, None)


def test_outranked_is_amber_and_counted_only_for_known_skills():
    assert validate.verdict([], 10, outranked=1).startswith("AMBER")
    rows = [{"rank": 2, "ms": 1, "reply_tokens": 1, "read_tokens": 1, "degraded": False, "grade": "outranked"},
            {"rank": 2, "ms": 1, "reply_tokens": 1, "read_tokens": 1, "degraded": False}]
    summary = validate.summarise(rows, 0)
    assert (summary["known"], summary["known_found"], summary["known_first"], summary["outranked"]) == (1, 1, 0, 1)


def test_describe_separates_what_to_judge_from_accepted_matches():
    text = validate.describe(known(["skill:a/sibling", "skill:z/other", "skill:a/right"]))
    assert "judge these: skill:z/other" in text and "close matches: skill:a/sibling" in text
    assert "at rank 2 of 50" in validate.describe(known(["skill:z/other"], deep=["skill:z/other", "skill:a/right"]))


def test_compare_shows_a_known_skill_that_moved():
    row = {"query": "a", "rank": 3, "grade": "outranked"}
    before, after = {"summary": {}, "rows": [row]}, {"summary": {}, "rows": [row | {"rank": 1, "grade": "first"}]}
    assert any("rank 3 outranked -> rank 1 first" in line for line in validate.compare(before, after))
    assert not any("moved" in line for line in validate.compare(before, before))
