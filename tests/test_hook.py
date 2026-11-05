"""The connector's prompt hook: quiet unless the server is confident."""
import importlib.util
import io
import json
from pathlib import Path

import pytest

path = Path(__file__).parent.parent / "plugins/canopy-connector/hooks/suggest.py"
spec = importlib.util.spec_from_file_location("suggest", path)
suggest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suggest)

HITS = [{"id": f"skill:p/s{i}", "description": "x" * 200} for i in range(5)]


def test_a_confident_reply_names_three_short_lines():
    out = suggest.render({"results": HITS, "confidence": 0.7})
    assert out.count("\n- ") == 3 and "skill:p/s2" in out and "skill:p/s3" not in out
    assert max(len(l) for l in out.splitlines()) < 100


@pytest.mark.parametrize("reply", [
    {"results": HITS, "confidence": 0.5, "weak": True},
    {"results": HITS},                                   # keyword only
    {"results": [], "confidence": 0.7},
])
def test_anything_else_is_silent(reply):
    assert suggest.render(reply) == ""


@pytest.mark.parametrize("prompt", ["continue", "yes go ahead"[:6], "/skill find something now"])
def test_short_prompts_and_commands_never_search(prompt, monkeypatch, capsys):
    monkeypatch.setenv("CANOPY_MCP_URL", "http://stand-in/mcp")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": prompt})))
    monkeypatch.setattr(suggest, "search", lambda *a: pytest.fail("searched"))
    suggest.main()
    assert capsys.readouterr().out == ""


def test_no_url_means_no_work(monkeypatch):
    monkeypatch.delenv("CANOPY_MCP_URL", raising=False)
    monkeypatch.setattr(suggest, "search", lambda *a: pytest.fail("searched"))
    suggest.main()


def test_events_and_plain_json_both_parse():
    assert suggest.parse('event: message\ndata: {"a": 1}\n\n') == {"a": 1}
    assert suggest.parse('{"a": 1}') == {"a": 1}
