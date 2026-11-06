import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).parent.parent / "plugins/canopy-manager/skills/claude-md-snippet/scripts/snippet.py"
spec = importlib.util.spec_from_file_location("snippet", path)
snippet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snippet)


def test_appends_keeps_the_rest_and_is_stable():
    once = snippet.apply("# Mine\n")
    assert once.startswith("# Mine\n\n") and snippet.BEGIN in once
    assert snippet.apply(once) == once


def test_an_empty_file_gets_only_the_block():
    assert snippet.apply("").startswith(snippet.BEGIN)


def test_replaces_only_its_block():
    old = f"before\n{snippet.BEGIN}\nold\n{snippet.END}\nafter\n"
    new = snippet.apply(old)
    assert "old" not in new and new.startswith("before\n") and new.endswith("after\n")


def test_remove_restores_the_surroundings():
    assert snippet.apply(snippet.apply("# Mine\n"), remove=True).strip() == "# Mine"
    assert snippet.apply("# Mine\n", remove=True) == "# Mine\n"


@pytest.mark.parametrize("text", [
    f"{snippet.BEGIN}\nmine\n",
    f"mine\n{snippet.END}\n",
    f"{snippet.END}\nmine\n{snippet.BEGIN}\n",
    f"{snippet.BEGIN}\n{snippet.BEGIN}\n{snippet.END}\n",
])
def test_broken_markers_are_refused_not_matched_across(text):
    with pytest.raises(ValueError):
        snippet.apply(text)
