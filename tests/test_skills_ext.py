"""The MCP Skills extension, against a skill folder on disk."""
import hashlib

import pytest

from canopy.skills_ext import EXTENSION, PAGE, Skills


@pytest.fixture
def skills(tmp_path):
    root = tmp_path / "plug"
    d = root / "skills/trim"
    (d / "references").mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: trim\ndescription: Trim a video.\n---\n\nBody\n")
    (d / "references/notes.md").write_text("notes")
    (d / ".hidden").write_text("no")
    (root / "secret.txt").write_text("outside the skill")
    row = {"id": "skill:plug/trim", "plugin": "plug", "name": "trim", "path": "skills/trim/SKILL.md"}
    rows = [row]
    return Skills(lambda offset, limit: rows[offset:offset + limit],
                  lambda p, n: row if (p, n) == ("plug", "trim") else None, lambda _: root)


def call(s, method, **params):
    return s.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


def test_initialize_declares_the_extension_and_resources(skills):
    caps = call(skills, "initialize")["result"]["capabilities"]
    assert EXTENSION in caps["extensions"] and "resources" in caps


def test_list_gives_frontmatter_and_digests(skills):
    out = call(skills, "skills/list")["result"]
    entry = out["skills"][0]
    assert out["resultType"] == "complete" and "nextCursor" not in out
    assert entry["uri"] == "skill://plug/trim/SKILL.md" and entry["frontmatter"]["name"] == "trim"
    by_uri = {r["uri"]: r for r in entry["resources"]}
    assert set(by_uri) == {"skill://plug/trim/SKILL.md", "skill://plug/trim/references/notes.md"}
    notes = by_uri["skill://plug/trim/references/notes.md"]
    assert notes["digest"] == "sha256:" + hashlib.sha256(b"notes").hexdigest() and notes["size"] == 5


def test_list_pages(tmp_path):
    root = tmp_path
    (root / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n")
    rows = [{"id": str(i), "plugin": "p", "name": "s", "path": "SKILL.md"} for i in range(PAGE + 1)]
    s = Skills(lambda o, n: rows[o:o + n], lambda *a: None, lambda _: root)
    first = call(s, "skills/list")["result"]
    assert len(first["skills"]) == PAGE and first["nextCursor"] == str(PAGE)
    assert len(call(s, "skills/list", cursor=first["nextCursor"])["result"]["skills"]) == 1


def test_get_and_an_unknown_uri(skills):
    assert call(skills, "skills/get", uri="skill://plug/trim/SKILL.md")["result"]["skill"]["frontmatter"]
    assert call(skills, "skills/get", uri="skill://plug/none/SKILL.md")["error"]["code"] == -32602


def test_read_returns_a_file(skills):
    out = call(skills, "resources/read", uri="skill://plug/trim/references/notes.md")["result"]
    assert out["contents"][0]["text"] == "notes"


@pytest.mark.parametrize("rel", ["../../secret.txt", "../trim/../../secret.txt", "references/../../../secret.txt"])
def test_read_cannot_leave_the_skill_folder(skills, rel):
    assert call(skills, "resources/read", uri=f"skill://plug/trim/{rel}")["error"]["code"] == -32602


def test_a_notification_gets_no_reply_and_an_unknown_method_an_error(skills):
    assert skills.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert call(skills, "tools/list")["error"]["code"] == -32601


def one_skill(tmp_path, text: str, name: str = "trim") -> Skills:
    (tmp_path / "SKILL.md").write_text(text)
    row = {"id": f"skill:p/{name}", "plugin": "p", "name": name, "path": "SKILL.md"}
    return Skills(lambda o, n: [row][o:o + n], lambda p, n: row if n == name else None, lambda _: tmp_path)


@pytest.mark.parametrize("text,name", [
    ("---\nname: trim\ndescription: Trim: a video: fast\n---\n", "trim"),   # YAML a host cannot parse
    ("---\ndescription: no name\n---\n", "trim"),                          # name came from the folder
    ("---\nname: Trim_Video\ndescription: d\n---\n", "Trim_Video"),         # not an Agent Skills name
    ("no frontmatter\n", "trim"),
])
def test_a_skill_a_host_would_refuse_is_left_out_and_not_served(tmp_path, text, name):
    s = one_skill(tmp_path, text, name)
    assert call(s, "skills/list")["result"]["skills"] == []
    err = call(s, "skills/get", uri=f"skill://p/{name}/SKILL.md")["error"]
    assert err["code"] == -32602 and "not served as an MCP skill" in err["message"]


def test_frontmatter_is_the_yaml_as_written_with_dates_as_text(tmp_path):
    s = one_skill(tmp_path, "---\nname: trim\ndescription: d\nmetadata:\n  updated: 2026-11-28\n---\n")
    fm = call(s, "skills/list")["result"]["skills"][0]["frontmatter"]
    assert fm == {"name": "trim", "description": "d", "metadata": {"updated": "2026-11-28"}}


def test_every_file_is_listed_with_a_digest_never_dynamic(tmp_path):
    s = one_skill(tmp_path, "---\nname: trim\ndescription: d\n---\n")
    for i in range(3):
        (tmp_path / f"f{i}.txt").write_text("x")
    entry = call(s, "skills/list")["result"]["skills"][0]
    assert isinstance(entry["resources"], list) and len(entry["resources"]) == 4
