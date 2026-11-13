"""CANOPY_OVERLAY, settings.env and the env.sh hook. A bare repo in a temp folder is the remote."""
import importlib.machinery
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PATH = Path(__file__).parent.parent / "clients" / "canopy"
loader = importlib.machinery.SourceFileLoader("canopy_cli_overlay", str(PATH))
spec = importlib.util.spec_from_loader("canopy_cli_overlay", loader)
cli = importlib.util.module_from_spec(spec)
loader.exec_module(cli)

REAL_SETTING = cli.setting   # the autouse fixture swaps it for an env-only stub
SECRET = "s3cr3t-value-never-shown"


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    """No user or system git config, and nothing cached from another test."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("CANOPY_AGENT_DATA", "CANOPY_OVERLAY", "CANOPY_OVERLAY_REF", "CANOPY_NO_HOOKS", "GITHUB_TOKEN", "GIT_CONFIG_COUNT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "setting", lambda name, default: cli.os.environ.get(name) or default)
    monkeypatch.setenv("CANOPY_DATA", str(tmp_path / "data"))
    cli.HOOK_RUNS.clear()
    cli.NOTICES.clear()
    yield
    cli.HOOK_RUNS.clear()


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


class Remote:
    def __init__(self, root: Path):
        self.bare, self.work = root / "remote.git", root / "work"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.bare)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.work)], check=True)
        git(self.work, "remote", "add", "origin", self.url)

    @property
    def url(self) -> str:
        return f"file://{self.bare}"

    def commit(self, files: dict[str, str], branch: str = "main", mode: int | None = None) -> str:
        git(self.work, "checkout", "-q", "-B", branch)
        for name, text in files.items():
            path = self.work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            if mode:
                path.chmod(mode)
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", f"{branch}: {sorted(files)}")
        git(self.work, "push", "-q", "-f", "origin", branch)
        return git(self.work, "rev-parse", "HEAD")


@pytest.fixture
def remote(tmp_path, monkeypatch):
    r = Remote(tmp_path)
    r.commit({"config/servers.json": "one", "settings.env": "CANOPY_TAG=v1\n"})
    monkeypatch.setenv("CANOPY_OVERLAY", r.url)
    return r


def clone(tmp_path) -> Path:
    return tmp_path / "data" / "overlay"


# --- the managed clone -----------------------------------------------------------------------

def test_clones_then_fast_forwards(tmp_path, remote):
    cli.overlay_sync()
    c = clone(tmp_path)
    assert (c / "config/servers.json").read_text() == "one"
    assert git(c, "symbolic-ref", "--short", "HEAD") == "main"
    assert (c / "cli").is_dir() and (tmp_path / "data/agent-state/cli").is_dir()
    head = remote.commit({"config/servers.json": "two"})
    cli.overlay_sync()
    assert (c / "config/servers.json").read_text() == "two"
    assert git(c, "rev-parse", "HEAD") == head
    assert git(c, "status", "--porcelain") == ""   # the empty cli/ mount point is invisible to git


def test_ref_can_be_a_branch_a_tag_or_a_commit(tmp_path, remote, monkeypatch):
    first = git(remote.work, "rev-parse", "HEAD")
    git(remote.work, "tag", "v1")
    git(remote.work, "push", "-q", "origin", "v1")
    remote.commit({"config/servers.json": "two"})
    remote.commit({"config/servers.json": "staged"}, branch="staging")
    c = clone(tmp_path)

    monkeypatch.setenv("CANOPY_OVERLAY_REF", "v1")
    cli.overlay_sync()
    assert (c / "config/servers.json").read_text() == "one"
    assert subprocess.run(["git", "-C", str(c), "symbolic-ref", "-q", "HEAD"]).returncode != 0   # detached

    monkeypatch.setenv("CANOPY_OVERLAY_REF", "staging")
    cli.overlay_sync()
    assert (c / "config/servers.json").read_text() == "staged"
    assert git(c, "symbolic-ref", "--short", "HEAD") == "staging"

    monkeypatch.setenv("CANOPY_OVERLAY_REF", first)
    cli.overlay_sync()
    assert git(c, "rev-parse", "HEAD") == first

    monkeypatch.setenv("CANOPY_OVERLAY_REF", "no-such-ref")
    with pytest.raises(SystemExit, match="no branch, tag or commit"):
        cli.overlay_sync()


@pytest.mark.parametrize("change", ["edit", "untracked"])
def test_a_clone_with_local_changes_is_not_touched(tmp_path, remote, change):
    cli.overlay_sync()
    c = clone(tmp_path)
    if change == "edit":
        (c / "config/servers.json").write_text("by hand")
    else:
        (c / "config/extra.json").write_text("{}")
    remote.commit({"config/servers.json": "two"})
    with pytest.raises(SystemExit, match="local changes.*commit the change to the overlay repo"):
        cli.overlay_sync()
    assert (c / "config/servers.json").read_text() == ("by hand" if change == "edit" else "one")


def test_a_clone_with_local_commits_is_not_touched(tmp_path, remote):
    cli.overlay_sync()
    c = clone(tmp_path)
    (c / "config/servers.json").write_text("local")
    git(c, "commit", "-qam", "local")
    with pytest.raises(SystemExit, match="commits that are not on the remote"):
        cli.overlay_sync()


def test_status(tmp_path, remote, capsys):
    assert cli.overlay_status() == 0
    assert "none yet" in capsys.readouterr().out
    cli.overlay_sync()
    assert cli.overlay_status() == 0
    out = capsys.readouterr().out
    assert "clean" in out and "0 behind, 0 ahead of origin/main" in out
    (clone(tmp_path) / "config/servers.json").write_text("by hand")
    assert cli.overlay_status() == 1
    assert "LOCAL CHANGES" in capsys.readouterr().out


def test_url_credentials_are_masked():
    assert cli.shown_url("https://me:tok@example.com/x.git") == "https://***@example.com/x.git"


# --- where things come from ------------------------------------------------------------------

def test_overlay_takes_precedence_over_agent_data(tmp_path, remote, monkeypatch, capsys):
    agent = tmp_path / "agent"
    (agent / "config").mkdir(parents=True)
    (agent / "compose.override.yaml").write_text("services: {}\n")
    (agent / "settings.env").write_text("X=1\n")
    monkeypatch.setenv("CANOPY_AGENT_DATA", str(agent))
    remote.commit({"compose.override.yaml": "services: {}\n"})
    cli.overlay_sync()
    args, env = cli.compose_args(), cli.compose_env()
    c, data = clone(tmp_path), tmp_path / "data"
    assert not any(str(agent) in a for a in args)
    assert str(c / "settings.env") in args and str(c / "compose.override.yaml") in args
    assert str(cli.REPO / "compose.overlay.yaml") in args
    assert env["CANOPY_AGENT_DATA"] == str(c) and env["CANOPY_AGENT_STATE"] == str(data / "agent-state")
    assert capsys.readouterr().err.count("CANOPY_OVERLAY is set") == 1   # once, not per call


def test_without_overlay_nothing_changes(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    monkeypatch.setenv("CANOPY_AGENT_DATA", str(agent))
    (agent / "config").mkdir(parents=True)
    (agent / "secrets.env").write_text("A=1\n")
    assert cli.compose_args() == ["docker", "compose", "--project-directory", str(cli.REPO),
                                  "--env-file", str(agent / "secrets.env"), "-f", str(cli.REPO / "compose.yaml")]
    assert cli.compose_env()["CANOPY_AGENT_DATA"] == str(agent)
    assert cli.overlay_sync().overlay == ""
    assert not (tmp_path / "data").exists()


def test_config_edits_are_refused_with_an_overlay(remote, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["canopy", "add-server", "x.json"])
    with pytest.raises(SystemExit, match="read-only here"):
        cli.main()


# --- settings.env < secrets.env < env.sh -------------------------------------------------------

HOOK = """#!/bin/sh
# a stand-in for a secrets manager
echo "# from the hook"
echo
echo "C=hook-$FROM_SECRETS"
echo "export D='quoted value'"
"""


@pytest.fixture
def agent(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    (agent / "config").mkdir(parents=True)
    monkeypatch.setenv("CANOPY_AGENT_DATA", str(agent))
    (agent / "settings.env").write_text("A=settings\nB=settings\nC=settings\n")
    (agent / "secrets.env").write_text("B=secrets\nC=secrets\nFROM_SECRETS=yes\n")
    return agent


def hook(folder: Path, text: str) -> None:
    (folder / "env.sh").write_text(text)
    (folder / "env.sh").chmod(0o755)


def test_settings_then_secrets_then_hook(agent, tmp_path):
    hook(agent, HOOK)
    args = cli.compose_args()
    files = [args[i + 1] for i, a in enumerate(args) if a == "--env-file"]
    assert files == [str(agent / "settings.env"), str(agent / "secrets.env")]   # later wins
    env = cli.compose_env()
    assert env["C"] == "hook-yes" and env["D"] == "quoted value"   # the hook sees secrets.env
    names = json.loads((tmp_path / "data/agent-state/hook-env.yaml").read_text())
    assert names["services"]["api"]["environment"] == ["C", "D"]
    assert "hook-yes" not in (tmp_path / "data/agent-state/hook-env.yaml").read_text()
    assert args[-1] == str(tmp_path / "data/agent-state/hook-env.yaml")   # last, so it wins


@pytest.mark.skipif(not shutil.which("docker"), reason="needs docker compose, which only reads files here")
def test_compose_sees_settings_then_secrets_then_hook(agent):
    hook(agent, HOOK)
    done = subprocess.run([*cli.compose_args(), "config", "--format", "json", "api"], env=cli.compose_env(),
                          capture_output=True, text=True)
    if done.returncode and "not a docker command" in done.stderr:
        pytest.skip("no docker compose plugin")
    assert done.returncode == 0, done.stderr
    env = json.loads(done.stdout)["services"]["api"]["environment"]
    # settings.env feeds compose substitution only: a name set only there never reaches the container
    assert "A" not in env
    assert (env["B"], env["C"]) == ("secrets", "hook-yes")


def test_the_overlay_hook_runs_in_the_clone(tmp_path, remote):
    remote.commit({"env.sh": '#!/bin/sh\necho "WHERE=$(basename "$PWD")"\n'}, mode=0o755)
    cli.overlay_sync()
    assert cli.compose_env()["WHERE"] == "overlay"


def test_a_malformed_line_is_named_by_number_only(agent, capsys):
    hook(agent, f'#!/bin/sh\necho "GOOD={SECRET}"\necho "not a pair {SECRET}"\n')
    with pytest.raises(SystemExit) as e:
        cli.compose_env()
    assert "line 2" in str(e.value.code) and SECRET not in str(e.value.code)
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err


def test_a_failing_hook_stops_with_its_code_and_stderr(agent, capsys):
    hook(agent, f'#!/bin/sh\necho "X={SECRET}"\necho "cannot log in" >&2\nexit 3\n')
    with pytest.raises(SystemExit) as e:
        cli.compose_args()
    assert e.value.code == 3
    out = capsys.readouterr()
    assert "cannot log in" in out.err and SECRET not in out.out + out.err


def test_no_hooks(agent, monkeypatch):
    hook(agent, "#!/bin/sh\nexit 1\n")
    monkeypatch.setenv("CANOPY_NO_HOOKS", "1")
    assert cli.hook_env() == {}
    monkeypatch.delenv("CANOPY_NO_HOOKS")
    monkeypatch.setattr(sys, "argv", ["canopy", "--no-hooks", "help"])
    assert cli.main() == 0 and cli.os.environ["CANOPY_NO_HOOKS"] == "1"


def test_a_hook_that_is_not_executable_is_skipped(agent, capsys):
    (agent / "env.sh").write_text("#!/bin/sh\nexit 1\n")
    assert cli.hook_env() == {}
    assert "not executable" in capsys.readouterr().err


# --- GITHUB_TOKEN ----------------------------------------------------------------------------------

def test_github_token_reaches_git_by_environment_only(tmp_path, remote, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", SECRET)
    argv = []
    real = subprocess.run

    def run(cmd, *a, **kw):
        argv.append(cmd)
        return real(cmd, *a, **kw)

    monkeypatch.setattr(cli.subprocess, "run", run)
    cli.overlay_sync()
    remote.commit({"config/servers.json": "two"})
    cli.overlay_sync()
    assert argv and not any(SECRET in " ".join(map(str, c)) for c in argv)
    for f in (clone(tmp_path) / ".git").rglob("*"):
        if f.is_file():
            assert SECRET.encode() not in f.read_bytes(), f


def test_github_token_is_sent_to_github_https_only(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", SECRET)
    place = cli.Place(tmp_path, tmp_path, tmp_path, "https://github.com/you/overlay.git")
    env = cli.overlay_git_env(place)
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"
    assert env["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ") and SECRET not in env["GIT_CONFIG_VALUE_0"]
    for url in ("git@github.com:you/overlay.git", "https://example.com/you/overlay.git", "file:///x"):
        assert "GIT_CONFIG_COUNT" not in cli.overlay_git_env(place._replace(overlay=url))


COUNTING_HOOK = """#!/bin/sh
echo run >> "$COUNTER"
echo "C=hook"
"""


def test_the_hook_runs_once_per_canopy_call(agent, tmp_path, monkeypatch):
    hook(agent, COUNTING_HOOK)
    counter = tmp_path / "runs"
    monkeypatch.setenv("COUNTER", str(counter))
    ran = []
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd, env=None: ran.append((cmd, env)) or 0)
    monkeypatch.setattr(sys, "argv", ["canopy", "compose", "config"])
    assert cli.main() == 0
    assert counter.read_text().count("run") == 1
    assert ran[0][1]["C"] == "hook" and ran[0][0][-1] == "config"


@pytest.mark.skipif(not shutil.which("docker"), reason="needs docker compose, which only reads files here")
def test_the_hook_runs_once_in_a_real_canopy_compose_config(agent, tmp_path):
    hook(agent, COUNTING_HOOK)
    counter = tmp_path / "runs"
    env = {**cli.os.environ, "COUNTER": str(counter)}
    done = subprocess.run([str(PATH), "compose", "config", "--quiet"], env=env, capture_output=True, text=True)
    if done.returncode and "not a docker command" in done.stderr:
        pytest.skip("no docker compose plugin")
    assert done.returncode == 0, done.stderr
    assert counter.read_text().count("run") == 1


def test_settings_env_never_reaches_a_container_environment():
    """settings.env is for compose substitution only. A host-side name such as CANOPY_API_PORT (the published
    port) means the listen port inside the api container, so passing the file in broke the health check (v1.02)."""
    import yaml
    root = Path(__file__).resolve().parents[1]
    for name in ("compose.yaml", "compose.overlay.yaml"):
        doc = yaml.safe_load((root / name).read_text())
        for service, spec in (doc.get("services") or {}).items():
            files = spec.get("env_file") or []
            paths = [f["path"] if isinstance(f, dict) else f for f in (files if isinstance(files, list) else [files])]
            assert not any(p.endswith("settings.env") for p in paths), f"{name}: {service} loads settings.env"


def test_the_cli_reads_its_own_settings_from_settings_env(agent, monkeypatch):
    """canopy talks to the API on CANOPY_API_PORT; with no wrapper exporting it, settings.env must supply it."""
    (agent / "settings.env").write_text("CANOPY_API_PORT=8825\nCANOPY_DATA=/should/not/be/used\n")
    monkeypatch.delenv("CANOPY_API_PORT", raising=False)
    monkeypatch.setattr(cli, "setting", REAL_SETTING)
    monkeypatch.setattr(cli, "REPO", agent.parent)   # no checkout .env in play
    assert cli.setting("CANOPY_API_PORT", "8090") == "8825"
    monkeypatch.setenv("CANOPY_API_PORT", "9000")
    assert cli.setting("CANOPY_API_PORT", "8090") == "9000"            # the process environment still wins
    assert cli.setting("CANOPY_DATA", "default") != "/should/not/be/used"   # locating names never come from it
