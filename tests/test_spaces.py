"""Spaces and profiles, on the household in docs/spaces.md."""
import pytest

from canopy.spaces import Spaces, SpacesError, load_tokens

DOC = {
    "operator": "daniel",
    "spaces": {"daniel": {"members": ["daniel"]}, "ridgeline": {"members": ["daniel"]},
               "hannah": {"members": ["hannah"]}, "willow": {"members": ["hannah"]},
               "household": {"members": ["daniel", "hannah"]}, "common": {"members": ["daniel", "hannah"]}},
    "profiles": {"daniel": {"person": "daniel", "spaces": ["daniel", "ridgeline", "household", "common"]},
                 "daniel-work": {"person": "daniel", "spaces": ["ridgeline", "common"]},
                 "hannah": {"person": "hannah", "spaces": ["hannah", "willow", "household", "common"]}},
}
SERVERS = [{"name": "gws-personal"}, {"name": "invoices", "space": "ridgeline"},
           {"name": "booking", "space": "willow"}, {"name": "home-assistant", "space": "household"},
           {"name": "time", "space": "common"}]
MARKETS = {"marketplaces": [{"name": "mine"}, {"name": "house-skills", "space": "household"}],
           "context": [{"repo": "someone/hannah-notes", "space": "hannah"}]}


def test_each_profile_sees_the_union_of_its_spaces():
    p = Spaces(DOC, SERVERS, MARKETS).profiles
    assert p["daniel"].scope.servers == ("gws-personal", "home-assistant", "invoices", "time")
    assert p["daniel-work"].scope.servers == ("invoices", "time")
    assert p["hannah"].scope.servers == ("booking", "home-assistant", "time")
    assert p["hannah"].scope.marketplaces == ("house-skills",)
    assert p["hannah"].scope.plugins == ("hannah-notes",)
    assert p["daniel"].scope.plugins == ("user",)   # the volume's own context is the operator's
    assert p["daniel-work"].scope.plugins == ()


@pytest.mark.parametrize("change, message", [
    (lambda d, s, m: d["profiles"]["hannah"]["spaces"].append("ridgeline"), "not a member of space 'ridgeline'"),
    (lambda d, s, m: s.append({"name": "x", "space": "nowhere"}), "unknown space 'nowhere'"),
    (lambda d, s, m: s.append({"name": "time", "space": "household"}), "listed twice"),
    (lambda d, s, m: d["profiles"]["daniel"].update(spaces=["attic"]), "unknown space 'attic'"),
    (lambda d, s, m: d["spaces"].pop("daniel"), "needs a space of the same name"),
])
def test_an_inconsistent_config_is_refused(change, message):
    import copy
    d, s, m = copy.deepcopy(DOC), copy.deepcopy(SERVERS), copy.deepcopy(MARKETS)
    change(d, s, m)
    with pytest.raises(SpacesError, match=message):
        Spaces(d, s, m)


def test_without_spaces_json_one_profile_sees_everything(tmp_path):
    sp = Spaces.load(tmp_path, SERVERS)
    assert list(sp.profiles) == ["default"] and sp.profiles["default"].scope is None


def test_tokens_are_made_once_and_kept(tmp_path):
    first = load_tokens(tmp_path, ["daniel", "hannah"])
    assert sorted(first.values()) == ["daniel", "hannah"]
    assert load_tokens(tmp_path, ["daniel", "hannah"]) == first
    assert (tmp_path / "profile-hannah.token").stat().st_mode & 0o777 == 0o600
