"""`canopy auto-update`: one crontab line per checkout, other lines left alone."""
import importlib.machinery
import importlib.util
import subprocess
from pathlib import Path

import pytest

PATH = Path(__file__).parent.parent / "clients" / "canopy"
loader = importlib.machinery.SourceFileLoader("canopy_cli_auto", str(PATH))
spec = importlib.util.spec_from_loader("canopy_cli_auto", loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)


@pytest.fixture
def cron(monkeypatch, tmp_path):
    tab = {"lines": ["15 3 * * * /usr/local/bin/backup.sh"], "missing": False}

    def run(cmd, input=None, capture_output=False, text=False, check=False):
        if cmd == ["crontab", "-l"]:
            if tab["missing"]:
                return subprocess.CompletedProcess(cmd, 1, "", "no crontab for daniel\n")
            return subprocess.CompletedProcess(cmd, 0, "".join(f"{x}\n" for x in tab["lines"]), "")
        if cmd == ["crontab", "-"]:
            tab["lines"] = input.splitlines()
            tab["missing"] = False
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(cmd)

    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(cli, "places", lambda: cli.Place(tmp_path, tmp_path, tmp_path / "data", ""))
    monkeypatch.setattr(cli, "setting", lambda name, default: {"CANOPY_TAG": "v1"}.get(name, default))
    return tab


def test_on_adds_one_line_and_keeps_the_rest(cron):
    cli.auto_update(["on", "05:10"])
    cli.auto_update(["on", "05:10"])   # again: replaced, not doubled
    assert cron["lines"][0] == "15 3 * * * /usr/local/bin/backup.sh"
    mine = [x for x in cron["lines"] if cli.CRON_MARK in x]
    assert len(mine) == 1 and mine[0].startswith("10 5 * * * ") and " update >> " in mine[0]


def test_off_removes_only_its_line(cron):
    cli.auto_update(["on"])
    cli.auto_update(["off"])
    assert cron["lines"] == ["15 3 * * * /usr/local/bin/backup.sh"]


def test_no_crontab_yet_is_fine(cron):
    cron["missing"] = True
    cli.auto_update(["on"])
    assert len(cron["lines"]) == 1 and cron["lines"][0].startswith("30 4 * * * ")


def test_a_bad_time_changes_nothing(cron):
    with pytest.raises(SystemExit):
        cli.auto_update(["on", "25:00"])
    assert cron["lines"] == ["15 3 * * * /usr/local/bin/backup.sh"]


def test_status_warns_on_a_fixed_tag(cron, monkeypatch, capsys):
    monkeypatch.setattr(cli, "setting", lambda name, default: {"CANOPY_TAG": "v1.10"}.get(name, default))
    cli.auto_update(["on"])
    cli.auto_update(["status"])
    assert "fixed release" in capsys.readouterr().out
