import importlib.util
import json
from pathlib import Path

import pytest

path = Path(__file__).parent.parent / "plugins/canopy-manager/skills/manage-clis/scripts/clis.py"
spec = importlib.util.spec_from_file_location("clis", path)
clis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clis)


def test_copy_logins_replaces_and_reports_missing(tmp_path):
    host, cli = tmp_path / "host", tmp_path / "cli"
    (host / ".config/tool").mkdir(parents=True)
    (host / ".config/tool/new").write_text("x")
    (cli / ".config/tool").mkdir(parents=True)
    (cli / ".config/tool/old").write_text("x")
    assert clis.copy_logins([".config/tool", ".config/none"], host, cli) == [".config/none"]
    assert [p.name for p in (cli / ".config/tool").iterdir()] == ["new"]


def test_wrapper_leaves_home_for_the_container():
    text = clis.wrapper("$HOME/.local/bin/t.bin", {"T_CONFIG": "$HOME/t.toml"})
    assert 'T_CONFIG="$HOME/t.toml" exec "$HOME/.local/bin/t.bin" "$@"' in text


def test_install_script_quotes_packages():
    assert "'a[cli]'" in clis.install_script({"name": "a", "via": "uv", "package": "a[cli]"})


def test_unknown_route_stops():
    with pytest.raises(SystemExit):
        clis.install_script({"name": "a", "via": "brew"})


LIBRARY = [{"name": "a", "group": "g", "about": "x", "via": "apk", "package": "a"},
           {"name": "b", "group": "g", "about": "x", "via": "npm", "package": "b", "check": "b -v"}]


def test_a_custom_entry_replaces_the_library_one():
    spec = {"tools": ["a", "b"], "clis": [{"name": "b", "via": "npm", "package": "b", "check": "b login"}]}
    assert [c.get("check") for c in clis.resolve(spec, LIBRARY) if c["name"] == "b"] == ["b login"]


def test_resolve_refuses_a_tool_not_in_the_library():
    with pytest.raises(SystemExit):
        clis.resolve({"tools": ["nope"]}, LIBRARY)


def test_toggle_takes_numbers_and_names():
    assert clis.toggle("1, b", LIBRARY, {"a"}) == {"b"}


def test_toggle_ignores_what_it_does_not_know(capsys):
    assert clis.toggle("9 nope", LIBRARY, {"a"}) == {"a"}
    assert "no such tool" in capsys.readouterr().out


def test_the_shipped_library_is_well_formed():
    tools = json.loads(clis.LIBRARY.read_text())["tools"]
    assert len({t["name"] for t in tools}) == len(tools)
    for tool in tools:
        assert {"name", "group", "about", "via", "check"} <= tool.keys()
        if tool["via"] != "bin" and tool["via"] != "tar":
            assert tool["package"]
