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

DEFAULT_TIMER_MESSAGES = {"timer": "Timer done: {label}", "timer_unlabelled": "Your {duration} timer is done",
                          "reminder": "Reminder: {text}"}


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
    # Delivery calls for this speaker's timers: [{tool, arguments}], run through their group. [] = no timers.
    timer_deliver: list[dict] = field(default_factory=list)
    # tool_discovery.search for this speaker, when their group's catalogue search is another tool (a scoped
    # catalogue). "" = the top-level tool_discovery.search.
    discovery_search: str = ""


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
    # Canopy tool discovery (discovery.py), the standard way: None = off. A list = on: these fnmatch patterns
    # name the tools sent natively, the group's Canopy catalogue search (`discovery_search`) finds the rest,
    # and the built-in call_tool runs them. Exclusive with tool_search.
    discovery_loaded: list[str] | None = None
    discovery_search: str = "catalogue__search"

    def search_tool(self, speaker: "Speaker") -> str:
        return speaker.discovery_search or self.discovery_search
    # One JSON line per turn (heard text, tool calls, reply, errors, timing) appended here; "" = no log.
    turn_log: str = ""
    # Timers and reminders (timers.py): the JSON file they persist in; "" = the timer tools are off.
    timers_store: str = ""
    timers_max_seconds: int = 7 * 86400
    timers_late_grace_s: int = 3600
    timers_poll_s: float = 15
    timers_messages: dict = field(default_factory=lambda: dict(DEFAULT_TIMER_MESSAGES))
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


def _tool_discovery(td) -> tuple[list[str] | None, str]:
    """`tool_discovery: {search: <group tool>, loaded: [patterns]}`; nothing or `enabled: false` = off."""
    if not td or (isinstance(td, dict) and td.get("enabled") is False):
        return None, "catalogue__search"
    if not isinstance(td, dict):
        raise ConfigError("tool_discovery: expected a mapping with `search` and `loaded`")
    loaded = td.get("loaded", [])
    if not isinstance(loaded, list) or not all(isinstance(x, str) for x in loaded):
        raise ConfigError("tool_discovery.loaded: expected a list of tool name patterns")
    search = td.get("search", "catalogue__search")
    if not isinstance(search, str) or not search:
        raise ConfigError("tool_discovery.search: expected the name of the group's catalogue search tool")
    return loaded, search


def _deliver(calls, where: str) -> list[dict]:
    """`deliver: [{tool, arguments}]` (a single mapping is one call)."""
    if calls is None:
        return []
    if isinstance(calls, dict):
        calls = [calls]
    if not isinstance(calls, list):
        raise ConfigError(f"{where}: expected a list of {{tool, arguments}}")
    out = []
    for i, c in enumerate(calls):
        if not isinstance(c, dict) or not isinstance(c.get("tool"), str) or not c["tool"]:
            raise ConfigError(f"{where}[{i}]: `tool` (a gateway tool name in the speaker's group) is required")
        args = c.get("arguments", {})
        if not isinstance(args, dict):
            raise ConfigError(f"{where}[{i}].arguments: expected a mapping")
        out.append({"tool": c["tool"], "arguments": args})
    return out


def load(path: str | Path, env: dict | None = None) -> Config:
    env = os.environ if env is None else env
    path = Path(path)
    doc = yaml.safe_load(path.read_text()) or {}
    base = path.parent

    def need(d: dict, key: str, where: str):
        if key not in d:
            raise ConfigError(f"{where}: `{key}` is required")
        return d[key]

    timers = doc.get("timers") or {}
    if not isinstance(timers, dict):
        raise ConfigError("timers: expected a mapping")
    default_deliver = _deliver(timers.get("deliver"), "timers.deliver")
    speakers = []
    for sid, s in (need(doc, "speakers", "voice.yaml") or {}).items():
        token_env = need(s, "token_env", f"speakers.{sid}")
        token = env.get(token_env, "")
        if not token:
            raise ConfigError(f"speakers.{sid}: environment variable {token_env} is not set")
        speakers.append(Speaker(id=sid, name=s.get("name", sid), group=need(s, "group", f"speakers.{sid}"),
                                token=token, address=s.get("address", s.get("name", sid)),
                                context=s.get("context", ""),
                                timer_deliver=_deliver((s.get("timers") or {}).get("deliver"),
                                                       f"speakers.{sid}.timers.deliver")
                                if "deliver" in (s.get("timers") or {}) else list(default_deliver),
                                discovery_search=str((s.get("tool_discovery") or {}).get("search") or "")))
    if len({s.token for s in speakers}) != len(speakers):
        raise ConfigError("two speakers share a token, so the token would not say who is speaking")

    discovery_loaded, discovery_search = _tool_discovery(doc.get("tool_discovery"))
    tool_search = _tool_search(doc.get("tool_search"))
    if discovery_loaded is not None and tool_search is not None:
        raise ConfigError("tool_discovery and tool_search are two ways to find tools: set one")
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
        tool_search_loaded=tool_search,
        discovery_loaded=discovery_loaded,
        discovery_search=discovery_search,
        turn_log=str(doc.get("turn_log") or ""),
        timers_store=str(timers.get("store") or ""),
        timers_max_seconds=int(timers.get("max_seconds", 7 * 86400)),
        timers_late_grace_s=int(timers.get("late_grace_s", 3600)),
        timers_poll_s=float(timers.get("poll_s", 15)),
        timers_messages={**DEFAULT_TIMER_MESSAGES, **(timers.get("messages") or {})},
        host=server.get("host", "0.0.0.0"),
        port=int(server.get("port", 8797)),
    )
