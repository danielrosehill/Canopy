import subprocess

import pytest

from canopy import index as ix
from canopy.usercontext import ContextError, UserContext


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def ctx(tmp_path):
    calls = []
    c = UserContext(root=tmp_path / "context", remote="", on_change=lambda: calls.append(1))
    c.calls = calls
    return c


def test_prepare_makes_a_repo_with_the_folders_once(ctx):
    assert ctx.prepare() == "created"
    for d in ("habits", "sops", "preferences", "context"):
        assert (ctx.root / d).is_dir()
    assert (ctx.root / "README.md").is_file() and ctx.head()
    assert ctx.prepare() == "ready"


def test_write_adds_frontmatter_commits_and_reindexes(ctx):
    ctx.prepare()
    before, n = ctx.head(), len(ctx.calls)
    out = ctx.write("preference", "hebrew-repos", "Hebrew repos go in Hebrish-ASR.", "Creating a Hebrew repo: use the org")
    assert out["id"] == f"preference:{ix.USER_CONTEXT_NAME}/hebrew-repos" and out["created"]
    assert ctx.head() != before and len(ctx.calls) == n + 1
    text = (ctx.root / "preferences/hebrew-repos.md").read_text()
    assert text.startswith("---\nid: hebrew-repos\ndescription: \"Creating a Hebrew repo: use the org\"")
    recs = ctx.listing()["records"]
    assert [r["description"] for r in recs] == ["Creating a Hebrew repo: use the org"]


def test_an_unchanged_write_makes_no_commit(ctx):
    ctx.prepare()
    ctx.write("habit", "terse", "Be brief.", "Always: be brief")
    head = ctx.head()
    text = ctx.read("habit", "terse")["text"]
    assert ctx.write("habit", "terse", text)["changed"] is False and ctx.head() == head


def test_write_needs_a_description_and_a_safe_name(ctx):
    ctx.prepare()
    with pytest.raises(ContextError, match="description"):
        ctx.write("sop", "deploy", "Steps")
    with pytest.raises(ContextError, match="name"):
        ctx.write("sop", "../escape", "x", "d")
    with pytest.raises(ContextError, match="kind"):
        ctx.write("skill", "x", "x", "d")


def test_delete_commits_and_history_keeps_it(ctx):
    ctx.prepare()
    ctx.write("context", "home-network", "Hosts.", "Home network hosts")
    ctx.delete("context", "home-network")
    assert not (ctx.root / "context/home-network.md").exists()
    assert "Remove context home-network" in git(ctx.root, "log", "--format=%s")
    with pytest.raises(ContextError) as e:
        ctx.delete("context", "home-network")
    assert e.value.status == 404


def test_push_rebases_onto_the_remote_and_pushes(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(remote), str(seed)], check=True)
    (seed / "habits").mkdir()
    (seed / "habits/a.md").write_text("---\ndescription: A\n---\n")
    (seed / "tools-data").mkdir()
    (seed / "tools-data/x.json").write_text("{}")
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "seed"); git(seed, "push", "-q", "origin", "HEAD:main")
    monkeypatch.setattr(ix, "git_url", lambda repo: str(remote))
    ctx = UserContext(root=tmp_path / "context", remote="me/User-Data")
    assert ctx.prepare() == "cloned"
    assert (ctx.root / "tools-data/x.json").exists()   # the rest of the repo comes along, unindexed
    ctx.write("habit", "b", "B", "B rule")
    # Someone else pushed meanwhile: the push rebases onto it.
    (seed / "habits/c.md").write_text("---\ndescription: C\n---\n")
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "other"); git(seed, "push", "-q", "origin", "HEAD:main")
    assert "pushed" in ctx.push()
    log = subprocess.run(["git", "--git-dir", str(remote), "log", "--format=%s", "main"],
                         capture_output=True, text=True).stdout
    assert log.splitlines()[0] == "Add habit b" and "other" in log


def test_push_without_a_remote_is_a_no_op(ctx):
    ctx.prepare()
    assert ctx.push().startswith("no CONTEXT_REMOTE")


def test_the_indexer_reads_the_volume_in_place(tmp_path, monkeypatch):
    ctx = UserContext(root=tmp_path / "context", remote="")
    ctx.prepare()
    ctx.write("sop", "release", "Steps.", "Cutting a release: the steps")
    monkeypatch.setattr(ix, "USER_CONTEXT", ctx.root)
    src = ix.user_context_source()
    assert src["repo"] == f"local:{ctx.root}" and src["marketplace"] == "context"
    assert ix.plugin_root(ix.USER_CONTEXT_NAME, src) == ctx.root
    heads, errors = ix.remote_heads({(src["repo"], None)})
    assert heads[(src["repo"], None)] == ctx.head() and not errors
    (ctx.root / "sops/draft.md").write_text("---\ndescription: d\n---\n")   # uncommitted edits still count
    assert ix.local_head(ctx.root) != ctx.head()
    recs = ix.records_in(ix.USER_CONTEXT_NAME, ctx.root, src)
    assert {r["id"] for r in recs} == {f"sop:{ix.USER_CONTEXT_NAME}/release", f"sop:{ix.USER_CONTEXT_NAME}/draft"}
