import json

import pytest

from canopy import index as ix


def make_plugin(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def test_extract_finds_skills_commands_agents(tmp_path):
    make_plugin(tmp_path, {
        "skills/a/SKILL.md": "---\nname: alpha\ndescription: Does a thing\n---\nBody",
        "commands/group/run.md": "Run it",
        "agents/helper.md": "---\ndescription: Helps\n---\n",
        "docs/other.md": "not a record",
    })
    ids = {r["id"]: r for r in ix.extract("p", tmp_path)}
    assert set(ids) == {"skill:p/alpha", "command:p/group:run", "agent:p/helper"}
    assert ids["skill:p/alpha"]["description"] == "Does a thing"


def test_extract_skips_vendored_plugin(tmp_path):
    make_plugin(tmp_path, {
        "skills/a/SKILL.md": "x",
        "vendor/other/.claude-plugin/plugin.json": "{}",
        "vendor/other/skills/b/SKILL.md": "y",
    })
    assert [r["name"] for r in ix.extract("p", tmp_path)] == ["a"]


def test_frontmatter_survives_unquoted_colon():
    fm, body = ix.frontmatter("---\nname: x\ndescription: use: when needed: always\n---\nBody")
    assert fm["name"] == "x" and body == "Body"


@pytest.mark.parametrize("bad", ["../evil", "a/b", "", "..", "-x", None, ".hidden", "a..b"])
def test_safe_name_rejects(bad):
    with pytest.raises(ValueError):
        ix.safe_name(bad)


@pytest.mark.parametrize("bad", ["--upload-pack=x", "-x", "a b", "a..b"])
def test_clean_ref_rejects_options(bad):
    with pytest.raises(ValueError):
        ix.clean_ref(bad)


@pytest.mark.parametrize("bad", ["file:///etc", "ext::sh -c id", "http://x/y.git", "--foo", "/etc/passwd"])
def test_git_url_rejects_other_transports(bad):
    with pytest.raises(ValueError):
        ix.git_url(bad)


def test_git_url_forms():
    assert ix.git_url("o/r").endswith("github.com/o/r.git")
    assert ix.git_url("https://git.example.com/o/r.git") == "https://git.example.com/o/r.git"


def test_parse_source_shapes():
    assert ix.parse_source("./plugins/x", "o/m") == {"repo": "o/m", "ref": None, "path": "plugins/x"}
    assert ix.parse_source({"source": "github", "repo": "o/r", "ref": "v1"}, "o/m")["ref"] == "v1"
    sub = ix.parse_source({"source": "git-subdir", "url": "https://github.com/o/r.git", "path": "p"}, "o/m")
    assert sub == {"repo": "o/r", "ref": None, "path": "p"}
    assert ix.parse_source("https://x", "o/m") is None
    with pytest.raises(ValueError):
        ix.parse_source("./../../etc", "o/m")
    with pytest.raises(ValueError):
        ix.parse_source({"source": "url", "url": "file:///etc"}, "o/m")


def test_redact_keeps_placeholders_only():
    out = ix._redact({"command": "x", "env": {"A": "${TOKEN}", "B": "literal-secret"}})
    assert out["env"] == {"A": "${TOKEN}", "B": "<set>"}


def test_mcp_references_use_client_name():
    refs = ix.mcp_references(f"call mcp__{ix.CLIENT_NAME}__srv__do_it and mcp__direct__tool",
                             {"srv"})
    assert (ix.CLIENT_NAME, "srv", "do_it") in refs and ("direct", "direct", "tool") in refs


def test_load_marketplaces_defaults_name(tmp_path, monkeypatch):
    monkeypatch.setattr(ix, "CONFIG", tmp_path)
    (tmp_path / "marketplaces.json").write_text(json.dumps({"marketplaces": [{"repo": "o/My-Repo"}]}))
    assert ix.load_marketplaces()[0]["name"] == "My-Repo"


def test_unreadable_marketplace_config_is_an_error_not_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ix, "CONFIG", tmp_path)
    plugins, errors, healthy = ix.read_manifests(fetch=False)
    assert plugins == {} and errors and healthy == set()


def test_a_missing_clone_is_stale_even_at_the_same_head(tmp_path, monkeypatch):
    monkeypatch.setattr(ix, "clones", lambda: tmp_path)
    row = {"head": "abc", "status": "ok"}
    assert ix.stale(row, "p", {}, "abc") is True    # what a restored database looks like
    (tmp_path / "p").mkdir()
    assert ix.stale(row, "p", {}, "abc") is False
    assert ix.stale(row, "p", {}, "def") is True
    assert ix.stale({**row, "status": "error"}, "p", {}, "abc", full=True) is True


def test_a_description_changed_in_the_marketplace_is_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(ix, "plugin_root", lambda n, m: tmp_path)
    row = {"head": "h", "status": "ok", "description": "old"}
    assert ix.stale(row, "p", {"description": "new"}, "h")
    assert not ix.stale(row, "p", {"description": "old"}, "h")


def test_a_plugin_that_failed_at_this_commit_waits_for_it_to_move_or_a_full_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(ix, "clones", lambda: tmp_path)
    (tmp_path / "p").mkdir()
    failed = {"head": "abc", "status": "error"}
    assert ix.stale(failed, "p", {}, "abc") is False
    assert ix.stale(failed, "p", {}, "abc", full=True) is True
    assert ix.stale(failed, "p", {}, "def") is True


class Reply:
    def __init__(self, doc):
        self.doc = doc

    def raise_for_status(self):
        pass

    def json(self):
        return self.doc


def test_graphql_heads_batches_and_resolves_tags_to_commits(monkeypatch):
    monkeypatch.setattr(ix, "GRAPHQL_BATCH", 2)
    queries = []

    def post(url, json, **kw):
        queries.append(json["query"])
        if len(queries) == 1:
            return Reply({"data": {
                "r0": {"defaultBranchRef": {"target": {"oid": "c" * 40}}},
                "r1": {"ref": {"target": {"oid": "t" * 40, "target": {"oid": "d" * 40}}}}}})
        return Reply({"data": {"r0": None}, "errors": [{"type": "NOT_FOUND"}]})

    keys = [("o/a", None), ("o/b", "v1"), ("o/gone", None)]
    heads = ix._graphql_heads(keys, token="t", post=post)
    assert len(queries) == 2
    assert 'repository(owner: "o", name: "b") { ref(qualifiedName: "v1")' in queries[0]
    assert heads == {("o/a", None): "c" * 40, ("o/b", "v1"): "d" * 40}   # the gone repo is left to git


def test_remote_heads_asks_git_for_what_graphql_left_and_needs_no_lookup_for_a_sha(monkeypatch):
    monkeypatch.setattr(ix, "GIT_TOKEN", "t")
    monkeypatch.setattr(ix, "_graphql_heads", lambda keys: {("o/a", None): "a" * 40})
    asked = []

    def git(repo, ref=None):
        asked.append(repo)
        if repo == "o/gone":
            raise RuntimeError("not found")
        return "b" * 40

    monkeypatch.setattr(ix, "remote_head", git)
    pinned = ("o/c", "e" * 40)
    heads, errors = ix.remote_heads({("o/a", None), ("https://example.org/x.git", None), ("o/gone", None), pinned})
    assert heads == {("o/a", None): "a" * 40, ("https://example.org/x.git", None): "b" * 40, pinned: "e" * 40}
    assert errors == {("o/gone", None): "not found"}
    assert sorted(asked) == ["https://example.org/x.git", "o/gone"]


def test_a_failed_graphql_call_falls_back_to_git(monkeypatch):
    monkeypatch.setattr(ix, "GIT_TOKEN", "t")

    def down(keys):
        raise RuntimeError("401")

    monkeypatch.setattr(ix, "_graphql_heads", down)
    monkeypatch.setattr(ix, "remote_head", lambda repo, ref=None: "f" * 40)
    assert ix.remote_heads({("o/a", None)}) == ({("o/a", None): "f" * 40}, {})
