import pytest

from canopy import seed


def test_expand_replaces_nested_values():
    out = seed.expand({"a": "x-${T}", "b": ["${T}"], "c": 1}, {"T": "v"})
    assert out == {"a": "x-v", "b": ["v"], "c": 1}


def test_expand_refuses_a_missing_variable():
    with pytest.raises(KeyError):
        seed.expand({"a": "${NOPE}"}, {})


def test_core_group_always_has_builtins():
    groups = seed.merged_groups([])
    assert groups[0]["name"] == "core"
    assert groups[0]["included_servers"] == ["catalogue", "toolbox"]


def test_user_core_group_keeps_its_members():
    groups = seed.merged_groups([{"name": "core", "included_tools": ["time__now"],
                                  "included_servers": ["extra"]}])
    assert groups[0]["included_servers"] == ["catalogue", "toolbox", "extra"]
    assert seed.group_servers(groups[0]) == {"catalogue", "toolbox", "extra", "time"}


def test_write_secret_is_private(tmp_path):
    p = tmp_path / "d" / "token"
    seed.write_secret(p, "abc")
    assert p.read_text() == "abc\n" and p.stat().st_mode & 0o077 == 0
