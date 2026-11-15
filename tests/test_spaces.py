"""Spaces and profiles, on the household in docs/spaces.md: ownership is per tool and per plugin."""
import copy

import pytest

from canopy.spaces import Placement, Spaces, SpacesError, load_tokens, resolve_scope

DOC = {
    "operator": "daniel",
    "spaces": {
        "daniel": {"members": ["daniel"]},
        "ridgeline": {"members": ["daniel"], "tools": ["invoices__*"], "marketplaces": ["ridgeline-skills"]},
        "hannah": {"members": ["hannah"], "plugins": ["hannah-notes"]},
        "willow": {"members": ["hannah"], "tools": ["booking__*"]},
        "household": {"members": ["daniel", "hannah"],
                      "tools": ["home-assistant__*", "inventory__search_items", "inventory__get_*"],
                      "marketplaces": ["house-skills"], "plugins": ["house-skills-admin*"]},
        "common": {"members": ["daniel", "hannah"], "tools": ["time__*", "*__ping"]},
    },
    "profiles": {"daniel": {"person": "daniel", "spaces": ["daniel", "ridgeline", "household", "common"]},
                 "daniel-work": {"person": "daniel", "spaces": ["ridgeline", "common"]},
                 "hannah": {"person": "hannah", "spaces": ["hannah", "willow", "household", "common"]}},
}


@pytest.mark.parametrize("tool, space", [
    ("inventory__search_items", "household"),    # exact name
    ("inventory__get_item", "household"),        # pattern
    ("inventory__delete_item", "daniel"),        # unclaimed: the operator's
    ("home-assistant__turn_on", "household"),    # a whole server is just a pattern
    ("box-control__run_command", "daniel"),
    ("invoices__ping", "ridgeline"),             # the longer pattern beats `*__ping`
    ("nas__ping", "common"),
])
def test_every_tool_resolves_to_one_space(tool, space):
    assert Spaces(DOC).placement.tool_space(tool) == space


@pytest.mark.parametrize("plugin, market, space", [
    ("lights", "house-skills", "household"),     # claimed marketplace
    ("house-skills-admin-tools", "house-skills", "household"),
    ("hannah-notes", "context", "hannah"),       # a context source, by name
    ("user", "context", "daniel"),               # the volume's own context: unclaimed, so the operator's
    ("ledger", "ridgeline-skills", "ridgeline"),
    ("diary", "mine", "daniel"),
])
def test_every_plugin_resolves_to_one_space(plugin, market, space):
    assert Spaces(DOC).placement.plugin_space(plugin, market) == space


def test_hannah_can_look_up_the_inventory_but_not_change_it():
    hannah = Spaces(DOC).profiles["hannah"]
    assert hannah.allows_tool("inventory__search_items") and hannah.allows_tool("inventory__get_item")
    assert not hannah.allows_tool("inventory__delete_item")
    assert not hannah.allows_tool("box-control__run_command")
    assert hannah.may_see_server("inventory")
    # A server that is down is listed if any of her patterns could match one of its tools: `*__ping` could.
    assert hannah.may_see_server("box-control")


def test_the_work_profile_leaves_the_household_out():
    work = Spaces(DOC).profiles["daniel-work"]
    assert work.allows_tool("invoices__send") and work.allows_tool("time__now")
    assert not work.allows_tool("home-assistant__turn_on") and not work.allows_tool("gws__send")


def test_the_catalogue_resolves_a_profiles_scope_from_the_request():
    req = Spaces(DOC).profiles["hannah"].scope_request("call_tool")
    sc = resolve_scope(req, [("lights", "house-skills"), ("diary", "mine"), ("hannah-notes", "context"),
                             ("user", "context")],
                       ["inventory__search_items", "inventory__delete_item", "booking__list", "gws__send"])
    assert sc.plugins == ("hannah-notes", "lights")
    assert sc.tools == ("booking__list", "inventory__search_items")
    assert sc.call == "call_tool"


def test_placement_survives_the_trip_to_the_catalogue():
    p = Spaces(DOC).placement
    q = Placement.from_dict(p.to_dict())
    for t in ("inventory__get_x", "invoices__ping", "nas__ping", "other__x"):
        assert q.tool_space(t) == p.tool_space(t)


@pytest.mark.parametrize("change, message", [
    (lambda d: d["profiles"]["hannah"]["spaces"].append("ridgeline"), "not a member of space 'ridgeline'"),
    (lambda d: d["spaces"]["hannah"].update(tools=["home-assistant__*"]), "claimed by two spaces"),
    (lambda d: d["spaces"]["willow"].update(tools=["inventory__search_items"]), "claimed by 'willow' and"),
    (lambda d: d["profiles"]["daniel"].update(spaces=["attic"]), "unknown space 'attic'"),
    (lambda d: d["spaces"].pop("daniel"), "needs a space of the same name"),
    (lambda d: d["spaces"]["common"].update(members=[]), "has no members"),
])
def test_an_inconsistent_config_is_refused(change, message):
    d = copy.deepcopy(DOC)
    change(d)
    with pytest.raises(SpacesError, match=message):
        Spaces(d)


def test_without_spaces_json_one_profile_sees_everything(tmp_path):
    p = Spaces.load(tmp_path).profiles
    assert list(p) == ["default"] and p["default"].allows_tool("anything__at_all")
    assert p["default"].scope_request("call_tool") is None


def test_tokens_are_made_once_and_kept(tmp_path):
    first = load_tokens(tmp_path, ["daniel", "hannah"])
    assert sorted(first.values()) == ["daniel", "hannah"]
    assert load_tokens(tmp_path, ["daniel", "hannah"]) == first
    assert (tmp_path / "profile-hannah.token").stat().st_mode & 0o777 == 0o600
