"""The logic around restic and Postgres. tests/contract/backup.sh runs the real tools."""
import json
import subprocess

import pytest

from canopy import backup as b
from canopy import scheduler as s


class Tools:
    def __init__(self, codes=None):
        self.calls, self.codes = [], codes or {}

    def __call__(self, cmd, env=None, **_):
        self.calls.append((cmd, env))
        return subprocess.CompletedProcess(cmd, self.codes.get(cmd[1], 0), "", "the reason")


def make(tmp_path, target=None, tools=None, monkeypatch=None):
    (tmp_path / "config").mkdir()
    if target is not None:
        (tmp_path / "config" / "backup.json").write_text(json.dumps(target))
    return b.Backups(config=tmp_path / "config", backups=tmp_path / "backups", agent_data=tmp_path / "agent",
                     state=[], run=tools or Tools())


def test_no_target_is_an_error_and_not_a_quiet_success(tmp_path, monkeypatch):
    monkeypatch.setenv("RESTIC_PASSWORD", "x")
    with pytest.raises(b.BackupError, match="no backup target"):
        make(tmp_path).restic("snapshots")


def test_no_password_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("RESTIC_PASSWORD", raising=False)
    with pytest.raises(b.BackupError, match="RESTIC_PASSWORD"):
        make(tmp_path, {"repository": "/r"}).restic("snapshots")


def test_the_target_reaches_restic(tmp_path, monkeypatch):
    monkeypatch.setenv("RESTIC_PASSWORD", "x")
    tools = Tools()
    make(tmp_path, {"repository": "s3:h/b", "region": "us-east-1", "options": {"s3.bucket-lookup": "path"}},
         tools).restic("snapshots")
    cmd, env = tools.calls[0]
    assert env["RESTIC_REPOSITORY"] == "s3:h/b" and env["AWS_DEFAULT_REGION"] == "us-east-1"
    assert cmd[-2:] == ["-o", "s3.bucket-lookup=path"]


def test_a_repository_is_made_only_when_there_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("RESTIC_PASSWORD", "x")
    tools = Tools({"cat": b.NO_REPOSITORY})
    make(tmp_path, {"repository": "/r"}, tools).ensure_repository()
    assert [c[0][1] for c in tools.calls] == ["cat", "init"]


def test_a_wrong_password_never_starts_a_new_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("RESTIC_PASSWORD", "x")
    tools = Tools({"cat": 12})
    with pytest.raises(b.BackupError, match="the reason"):
        make(tmp_path, {"repository": "/r"}, tools).ensure_repository()
    assert [c[0][1] for c in tools.calls] == ["cat"]


def test_retention_covers_both_tiers_and_the_file_can_change_it(tmp_path, monkeypatch):
    monkeypatch.setenv("RESTIC_PASSWORD", "x")
    tools = Tools()
    make(tmp_path, {"repository": "/r", "keep": {"daily": 3}}, tools).forget()
    calls = [c[0] for c in tools.calls]
    assert [c[1] for c in calls] == ["forget", "forget", "prune"]
    assert calls[0][calls[0].index("--keep-daily") + 1] == "3" and "--keep-weekly" in calls[0]
    assert {c[c.index("--tag") + 1] for c in calls[:2]} == {"data", "config"}


def test_secret_names_never_carry_values(tmp_path):
    f = tmp_path / "secrets.env"
    f.write_text("# COMMENTED=no\nA_KEY=hunter2\nexport B_KEY=x=y\n\nnot a line\n")
    assert b.secret_names(f) == ["A_KEY", "B_KEY"]
    assert b.secret_names(tmp_path / "absent") == []


def test_backups_wait_for_a_target(tmp_path):
    jobs = {j.name: j for j in s.load(tmp_path)}
    assert not jobs["backup-data"].enabled and not jobs["backup-config"].enabled
    (tmp_path / "backup.json").write_text("{}")
    jobs = {j.name: j for j in s.load(tmp_path)}
    assert jobs["backup-data"].every == 86400 and jobs["backup-config"].every == 3600
    assert jobs["restore-test"].after == "backup-data" and jobs["restore-test"].enabled
