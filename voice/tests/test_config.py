import pytest

from canopy_voice.config import ConfigError, load


def write(tmp_path, text):
    p = tmp_path / "voice.yaml"
    p.write_text(text)
    return p


BASE = """
gateway: {url: "http://gateway:8080/", policy_url: "http://api:8090"}
prompt_file: prompt.md
speakers:
  alex: {name: Alex, group: voice_alex, token_env: T_ALEX}
  sam: {name: Sam, address: Mx Sam, group: voice_sam, token_env: T_SAM, context: "Sam runs the kitchen"}
"""


def test_tokens_come_from_the_environment_and_identify_the_speaker(tmp_path):
    (tmp_path / "prompt.md").write_text("Be brief.")
    cfg = load(write(tmp_path, BASE), {"T_ALEX": "a", "T_SAM": "s", "CANOPY_POLICY_TOKEN": "p"})
    assert cfg.gateway_url == "http://gateway:8080" and cfg.policy_token == "p" and cfg.prompt == "Be brief."
    assert cfg.speaker_for_token("s").address == "Mx Sam"
    assert cfg.speaker_for_token("nope") is None


def test_a_missing_token_is_an_error(tmp_path):
    (tmp_path / "prompt.md").write_text("x")
    with pytest.raises(ConfigError, match="T_SAM"):
        load(write(tmp_path, BASE), {"T_ALEX": "a"})


def test_two_speakers_cannot_share_a_token(tmp_path):
    (tmp_path / "prompt.md").write_text("x")
    with pytest.raises(ConfigError, match="share"):
        load(write(tmp_path, BASE), {"T_ALEX": "x", "T_SAM": "x"})


def test_tool_search_is_off_unless_configured_and_takes_loaded_patterns():
    import pytest
    from canopy_voice.config import ConfigError, _tool_search
    assert _tool_search(None) is None and _tool_search(False) is None
    assert _tool_search({"enabled": False, "loaded": ["a"]}) is None
    assert _tool_search(True) == []
    assert _tool_search({"loaded": ["home__*", "mail__send"]}) == ["home__*", "mail__send"]
    with pytest.raises(ConfigError):
        _tool_search({"loaded": "home__*"})
