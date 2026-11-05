"""The `canopy` command. It has no .py suffix, so it is loaded by path."""
import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).parent.parent / "clients" / "canopy"
loader = importlib.machinery.SourceFileLoader("canopy_cli", str(PATH))
spec = importlib.util.spec_from_loader("canopy_cli", loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


@pytest.fixture
def folders(tmp_path, monkeypatch):
    monkeypatch.setenv("CANOPY_AGENT_DATA", str(tmp_path / "agent"))
    monkeypatch.setenv("CANOPY_DATA", str(tmp_path / "data"))
    return tmp_path


def test_read_env_file(tmp_path):
    f = tmp_path / "e"
    f.write_text("# c\nA=1\nexport B='two'\n\nC=\"x=y\"\n")
    assert cli.read_env_file(f) == {"A": "1", "B": "two", "C": "x=y"}


def test_init_makes_folders_and_keeps_what_exists(folders):
    cli.init()
    assert (folders / "agent/config/marketplaces.json").is_file()
    assert (folders / "agent/secrets.env").stat().st_mode & 0o077 == 0
    assert (folders / "data/admin").stat().st_mode & 0o077 == 0
    (folders / "agent/config/marketplaces.json").write_text("mine")
    cli.init()
    assert (folders / "agent/config/marketplaces.json").read_text() == "mine"


def test_compose_uses_secrets_and_override_when_present(folders):
    cli.init()
    args = cli.compose_args()
    assert str(folders / "agent/secrets.env") in args
    assert not any("override" in a for a in args)
    (folders / "agent/compose.override.yaml").write_text("services: {}\n")
    assert str(folders / "agent/compose.override.yaml") in cli.compose_args()
