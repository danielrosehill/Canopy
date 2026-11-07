"""The `canopy` command. It has no .py suffix, so it is loaded by path."""
import importlib.machinery
import json
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


IMAGES = {"danielrosehill/canopy:v1": "old", "danielrosehill/canopy:cli-v1": "oldcli"}


@pytest.fixture
def stack(folders, monkeypatch):
    """A stand-in for docker and git: records what ran, and images change when pulled."""
    state = {"images": dict(IMAGES), "ran": [], "pull": 0, "healthy": [True, True], "dirty": "", "head": "aaa"}

    def capture(*cmd):
        if "status" in cmd:
            return state["dirty"]
        if "rev-parse" in cmd:
            return state["head"]
        if "tag" in cmd:
            return "v1.01\nv1.00"
        return ""

    def compose(*args):
        state["ran"].append(("compose", *args))
        if args[0] == "pull":
            if state["pull"]:
                return state["pull"]
            state["images"] = {n: i + "-new" for n, i in state["images"].items()} if state.get("newer", True) else state["images"]
        return 0

    def git(*args):
        state["ran"].append(("git", *args))
        if args[0] == "checkout" and state.get("newer", True):
            state["head"] = args[-1]
        return 0

    def backup_now():
        state["ran"].append(("backup",))
        return state.get("backup", True)

    monkeypatch.setattr(cli, "backup_now", backup_now)
    monkeypatch.setattr(cli, "canopy_images", lambda: dict(state["images"]))
    monkeypatch.setattr(cli, "capture", capture)
    monkeypatch.setattr(cli, "compose", compose)
    monkeypatch.setattr(cli, "git", git)
    monkeypatch.setattr(cli, "up", lambda args: state["ran"].append(("up",)) or 0)
    monkeypatch.setattr(cli, "healthy", lambda tries=60: state["healthy"].pop(0))
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd, **k: state["ran"].append(tuple(cmd)) or 0)
    monkeypatch.setattr(cli, "REPO", folders)
    (folders / ".git").mkdir()
    return state


def test_update_moves_to_the_newest_release_of_the_major(stack):
    assert cli.update() == 0
    assert ("git", "checkout", "-q", "--detach", "v1.01") in stack["ran"]
    assert ("up",) in stack["ran"]
    assert not any(step[0] == "docker" for step in stack["ran"])


def test_update_goes_back_when_the_new_version_is_unhealthy(stack):
    stack["healthy"] = [True, False, True]
    assert cli.update() == 1
    assert ("docker", "tag", "old", "danielrosehill/canopy:v1") in stack["ran"]
    assert ("docker", "tag", "oldcli", "danielrosehill/canopy:cli-v1") in stack["ran"]
    assert stack["ran"][-2:] == [("git", "checkout", "-q", "--detach", "aaa"), ("up",)]


def test_update_does_not_go_back_to_something_already_unhealthy(stack):
    stack["healthy"] = [False, False]
    assert cli.update() == 1
    assert not any(step[0] == "docker" for step in stack["ran"])


def test_a_failed_pull_leaves_the_stack_alone(stack):
    stack["pull"] = 1
    assert cli.update() == 1
    assert ("up",) not in stack["ran"]
    assert stack["ran"][-1] == ("git", "checkout", "-q", "--detach", "aaa")


def test_update_stops_on_local_changes(stack):
    stack["dirty"] = " M compose.yaml"
    with pytest.raises(SystemExit):
        cli.update()
    assert stack["ran"] == []


def test_update_with_nothing_new_restarts_nothing(stack):
    stack["newer"] = False
    assert cli.update() == 0
    assert ("up",) not in stack["ran"]


def test_the_example_overlay_is_valid():
    root = PATH.parent.parent / "examples/overlay"
    servers = json.loads((root / "config/servers.json").read_text())
    assert {s["name"] for s in servers["servers"]} == {"my-mcp", "my-hosted-mcp"}
    assert all("repo" in m for m in json.loads((root / "config/marketplaces.json").read_text())["marketplaces"])
    assert "secrets.env" in (root / ".gitignore").read_text()


# --- a backup before an update -----------------------------------------------------------

def test_update_backs_up_before_it_restarts_anything(stack):
    assert cli.update() == 0
    assert stack["ran"].index(("backup",)) < stack["ran"].index(("up",))


def test_a_failed_backup_stops_the_update_and_puts_the_checkout_back(stack):
    stack["backup"] = False
    assert cli.update() == 1
    assert ("up",) not in stack["ran"]
    assert stack["ran"][-1] == ("git", "checkout", "-q", "--detach", "aaa")


def test_no_backup_target_goes_ahead(stack):
    stack["backup"] = None
    assert cli.update() == 0 and ("up",) in stack["ran"]


def test_no_backup_skips_it_and_so_does_a_stack_that_is_down(stack):
    assert cli.update(["--no-backup"]) == 0 and ("backup",) not in stack["ran"]
    stack.update(ran=[], healthy=[False, True], head="aaa", images=dict(IMAGES))
    assert cli.update() == 0 and ("backup",) not in stack["ran"]


def test_nothing_to_update_means_no_backup(stack):
    stack["newer"] = False
    assert cli.update() == 0 and ("backup",) not in stack["ran"]


def replies(monkeypatch, runs_seen, post=(202, {})):
    """`runs_seen` is what each read of the job's runs returns, in turn."""
    def call(method, path, body=None, timeout=900):
        if path == "/v1/backup/config":
            return 200, {}
        if method == "POST":
            return post
        return 200, runs_seen.pop(0) if len(runs_seen) > 1 else runs_seen[0]
    monkeypatch.setattr(cli, "call", call)


def run(rid, finished=True, ok=True):
    return {"id": rid, "finished": "t" if finished else None, "ok": ok, "output": "out"}


def test_backup_now_waits_for_a_run_newer_than_the_last_one(monkeypatch):
    replies(monkeypatch, [[run(1)], [run(1)], [run(2, finished=False), run(1)], [run(2), run(1)]])
    assert cli.backup_now(poll=0) is True


def test_backup_now_waits_out_a_run_already_going(monkeypatch):
    replies(monkeypatch, [[run(2, finished=False), run(1)], [run(2), run(1)]])
    assert cli.backup_now(poll=0) is True


def test_backup_now_reports_a_failed_run(monkeypatch):
    replies(monkeypatch, [[], [run(1, ok=False)]])
    assert cli.backup_now(poll=0) is False


def test_backup_now_gives_up_in_time(monkeypatch):
    replies(monkeypatch, [[]])
    assert cli.backup_now(wait=0.01, poll=0) is False


def test_backup_now_with_no_target_is_none(monkeypatch):
    monkeypatch.setattr(cli, "call", lambda *a, **k: (409, {"error": "no backup target"}))
    assert cli.backup_now() is None

