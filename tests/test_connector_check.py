import importlib.util
from pathlib import Path

path = Path(__file__).parent.parent / "plugins/canopy-connector/skills/test/scripts/check.py"
spec = importlib.util.spec_from_file_location("check", path)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def test_a_failure_is_red_however_fast():
    assert check.verdict(["x"], 1).startswith("RED")


def test_slow_is_amber_and_fast_is_green():
    assert check.verdict([], check.SLOW_MS + 1).startswith("AMBER")
    assert check.verdict([], 10).startswith("GREEN")


def test_parse_reads_plain_json_and_events():
    assert check.parse('{"id": 1}') == {"id": 1}
    assert check.parse('event: message\ndata: {"id": 2}\n') == {"id": 2}
