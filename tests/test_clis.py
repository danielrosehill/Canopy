import importlib.util
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
