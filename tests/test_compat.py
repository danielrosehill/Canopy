import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_the_pinned_gateway_is_the_one_compose_runs_and_was_tested():
    compat = json.loads((ROOT / "compat.json").read_text())["mcpjungle"]
    compose_default = re.search(r"MCPJUNGLE_TAG:-([^}]+)\}", (ROOT / "compose.yaml").read_text())[1]
    assert compat["pinned"] == compose_default
    tested = next(t for t in compat["tested"] if t["tag"] == compat["pinned"])
    assert set(tested["modes"]) == {"enterprise", "development"}
