"""voice.yaml: the gateway, the model, speech, and one entry per speaker.

Secrets never go in the file. A speaker's bearer token comes from the environment variable its
`token_env` names, and provider keys (ANTHROPIC_API_KEY, ...) come from the environment too.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_YES = ["yes", "yeah", "yep", "yup", "sure", "ok", "okay", "go ahead", "do it", "confirm", "confirmed",
               "correct", "please do", "send it", "affirmative", "that's right"]
DEFAULT_NO = ["no", "nope", "cancel", "stop", "don't", "do not", "wait", "never mind", "hold on", "not"]


class ConfigError(ValueError):
    pass


@dataclass
class Speaker:
    id: str
    name: str
    group: str
    token: str
    address: str = ""
    context: str = ""


@dataclass
class Config:
    gateway_url: str                      # the gateway's base URL; groups are at /v0/groups/<name>/mcp
    policy_url: str                       # Canopy API base URL, for /v1/gateway/groups/<name>/policy
    policy_token: str
    gateway_token: str
    model: str
    effort: str | None
    max_tokens: int
    prompt: str
    timezone: str
    session_ttl_s: int
    stt: dict
    tts: dict
    speakers: list[Speaker]
    yes: list[str] = field(default_factory=lambda: list(DEFAULT_YES))
    no: list[str] = field(default_factory=lambda: list(DEFAULT_NO))
    max_confirm_words: int = 8
    # Claude's tool search: None = off (every tool's schema is sent and loaded). A list = on, and these
    # fnmatch patterns name the tools kept loaded; every other tool is deferred until Claude searches for it.
    tool_search_loaded: list[str] | None = None
    host: str = "0.0.0.0"
    port: int = 8797

    def speaker_for_token(self, token: str) -> Speaker | None:
        import hmac
        for s in self.speakers:
            if s.token and hmac.compare_digest(s.token.encode(), token.encode()):
                return s
        return None


def _tool_search(ts) -> list[str] | None:
    """`tool_search: {loaded: [patterns]}` turns it on; `false`, `{enabled: false}` or nothing leaves it off."""
    if not ts or (isinstance(ts, dict) and ts.get("enabled") is False):
        return None
    if ts is True:
        return []
    if not isinstance(ts, dict):
        raise ConfigError("tool_search: expected a mapping with `loaded`, or true/false")
    loaded = ts.get("loaded", [])
    if not isinstance(loaded, list) or not all(isinstance(x, str) for x in loaded):
        raise ConfigError("tool_search.loaded: expected a list of tool name patterns")
    return loaded


def load(path: str | Path, env: dict | None = None) -> Config:
    env = os.environ if env is None else env
    path = Path(path)
    doc = yaml.safe_load(path.read_text()) or {}
    base = path.parent

    def need(d: dict, key: str, where: str):
        if key not in d:
            raise ConfigError(f"{where}: `{key}` is required")
        return d[key]

    speakers = []
    for sid, s in (need(doc, "speakers", "voice.yaml") or {}).items():
        token_env = need(s, "token_env", f"speakers.{sid}")
        token = env.get(token_env, "")
        if not token:
            raise ConfigError(f"speakers.{sid}: environment variable {token_env} is not set")
        speakers.append(Speaker(id=sid, name=s.get("name", sid), group=need(s, "group", f"speakers.{sid}"),
                                token=token, address=s.get("address", s.get("name", sid)),
                                context=s.get("context", "")))
    if len({s.token for s in speakers}) != len(speakers):
        raise ConfigError("two speakers share a token, so the token would not say who is speaking")

    llm = doc.get("llm", {})
    prompt = doc.get("prompt", "")
    if doc.get("prompt_file"):
        p = Path(doc["prompt_file"])
        prompt = (p if p.is_absolute() else base / p).read_text()
    confirm = doc.get("confirm", {})
    server = doc.get("server", {})
    gw = doc.get("gateway", {})
    return Config(
        gateway_url=need(gw, "url", "gateway").rstrip("/"),
        policy_url=gw.get("policy_url", "").rstrip("/"),
        policy_token=env.get(gw.get("policy_token_env", "CANOPY_POLICY_TOKEN"), ""),
        gateway_token=env.get(gw.get("token_env", "VOICE_GATEWAY_TOKEN"), ""),
        model=llm.get("model", "claude-haiku-5-5"),
        effort=llm.get("effort", "low"),
        max_tokens=int(llm.get("max_tokens", 1024)),
        prompt=prompt or "You are a voice assistant. Keep replies short and speakable: no lists, no markdown.",
        timezone=doc.get("timezone", "UTC"),
        session_ttl_s=int(doc.get("session_ttl_s", 180)),
        stt=doc.get("stt", {}),
        tts=doc.get("tts", {}),
        speakers=speakers,
        yes=confirm.get("yes", list(DEFAULT_YES)),
        no=confirm.get("no", list(DEFAULT_NO)),
        max_confirm_words=int(confirm.get("max_words", 8)),
        tool_search_loaded=_tool_search(doc.get("tool_search")),
        host=server.get("host", "0.0.0.0"),
        port=int(server.get("port", 8797)),
    )
