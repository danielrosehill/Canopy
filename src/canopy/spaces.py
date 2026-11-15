"""Spaces and profiles: who sees which tools and plugins. The design, with a worked household, is docs/spaces.md.

The unit of ownership is a tool (`<server>__<tool>`) or a plugin, not a server or a machine: one server can
hold tools for everyone and tools for its admin only. An account (`<server>:<account>`, one key of a server
that has several) is owned the same way: tools say what a profile can do, accounts say as whom. A space claims tools and plugins by name or by pattern,
and a whole server or marketplace is only a pattern (`home-assistant__*`) or a shortcut (`marketplaces`).
Every tool and plugin resolves to exactly one space:

    an exact name  >  the longest matching pattern  >  a claimed marketplace (plugins)  >  the operator's space

A tie between two patterns of the same length goes to the space listed first. A profile is a person plus
some of the spaces they are a member of, and a token is issued to a profile. What a profile sees is
everything that resolves to one of its spaces.

`agent-data/config/spaces.json`:

    {"operator": "daniel",
     "spaces": {
       "daniel":    {"members": ["daniel"]},
       "household": {"members": ["daniel", "hannah"],
                     "tools": ["home-assistant__*", "inventory__search_items"],
                     "marketplaces": ["household-skills"], "plugins": ["hannah-notes"]}},
     "profiles": {"daniel": {"person": "daniel", "spaces": ["daniel", "household"]}, ...}}

The operator's space, named after the operator, holds everything nobody claimed, so it lists nothing.
With no spaces.json there is one profile, `default`, that sees everything.
"""
from __future__ import annotations

import json
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from .retrieval import Scope


class SpacesError(ValueError):
    """The config is inconsistent. The message names the entry."""


def _pattern(p: str) -> bool:
    return any(c in p for c in "*?[")


class Placement:
    """Which space every tool and plugin belongs to. Built from spaces.json's `spaces`; also travels to the
    catalogue (`to_dict`), which resolves its own plugin and tool lists with it."""

    def __init__(self, operator: str, spaces: dict[str, dict]):
        self.operator, self.order = operator, list(spaces)
        self.rules: dict[str, list[tuple[str, str]]] = {"tools": [], "plugins": [], "accounts": []}
        self.exact: dict[str, dict[str, str]] = {"tools": {}, "plugins": {}, "marketplaces": {}, "accounts": {}}
        for space, s in spaces.items():
            for kind in ("tools", "plugins", "marketplaces", "accounts"):
                for entry in s.get(kind) or ():
                    if kind != "marketplaces" and _pattern(entry):
                        if any(p == entry for p, _ in self.rules[kind]):
                            raise SpacesError(f"{kind} pattern {entry!r} is claimed by two spaces")
                        self.rules[kind].append((entry, space))
                    else:
                        if entry in self.exact[kind]:
                            raise SpacesError(f"{kind} {entry!r} is claimed by {self.exact[kind][entry]!r} "
                                              f"and {space!r}")
                        self.exact[kind][entry] = space
        for kind in self.rules:   # longest first; equal length keeps spaces.json order (sort is stable)
            self.rules[kind].sort(key=lambda r: -len(r[0]))

    def _match(self, kind: str, name: str) -> str | None:
        if name in self.exact[kind]:
            return self.exact[kind][name]
        return next((space for pat, space in self.rules[kind] if fnmatchcase(name, pat)), None)

    def tool_space(self, tool: str) -> str:
        return self._match("tools", tool) or self.operator

    def account_space(self, server: str, account: str) -> str:
        return self._match("accounts", f"{server}:{account}") or self.operator

    def plugin_space(self, plugin: str, marketplace: str = "") -> str:
        return self._match("plugins", plugin) or self.exact["marketplaces"].get(marketplace) or self.operator

    def server_spaces(self, server: str) -> set[str]:
        """Every space that may hold a tool of this server, for listing a server whose tools are unknown
        (it is down). The operator's space is always one: nothing proves every tool was claimed."""
        prefix = f"{server}__"
        out = {self.operator}
        out |= {sp for t, sp in self.exact["tools"].items() if t.startswith(prefix)}
        out |= {sp for p, sp in self.rules["tools"] if fnmatchcase(server, p.split("__", 1)[0])}
        return out

    def to_dict(self) -> dict:
        spaces: dict[str, dict] = {s: {"tools": [], "plugins": [], "marketplaces": [], "accounts": []}
                                   for s in self.order}
        for kind in ("tools", "plugins", "accounts"):
            for pat, sp in self.rules[kind]:
                spaces[sp][kind].append(pat)
        for kind, names in self.exact.items():
            for name, sp in names.items():
                spaces[sp][kind].append(name)
        return {"operator": self.operator, "spaces": spaces}

    @classmethod
    def from_dict(cls, d: dict) -> Placement:
        return cls(d["operator"], d["spaces"])


@dataclass(frozen=True)
class Profile:
    name: str
    person: str
    spaces: tuple[str, ...]
    placement: Placement | None   # None: sees everything (no spaces.json)
    defaults: tuple[tuple[str, str], ...] = ()   # (server, account)

    def accounts_for(self, server: str, every: list[str]) -> list[str]:
        if self.placement is None:
            return list(every)
        return [a for a in every if self.placement.account_space(server, a) in self.spaces]

    def default_account(self, server: str) -> str | None:
        return dict(self.defaults).get(server)

    def allows_tool(self, tool: str) -> bool:
        return self.placement is None or self.placement.tool_space(tool) in self.spaces

    def may_see_server(self, server: str) -> bool:
        return self.placement is None or bool(self.placement.server_spaces(server) & set(self.spaces))

    def scope_request(self, call: str) -> dict | None:
        """What the front door sends the catalogue, which resolves it with `resolve_scope`."""
        if self.placement is None:
            return None
        return {"placement": self.placement.to_dict(), "spaces": list(self.spaces), "call": call}


def resolve_scope(request: dict, plugins: Iterable[tuple[str, str]], tools: Iterable[str]) -> Scope:
    """A profile's scope as the catalogue's lists: `plugins` is every (plugin, marketplace) it indexes,
    `tools` every tool name. Done per request, so a new tool or plugin lands in the right space at once."""
    placement, visible = Placement.from_dict(request["placement"]), set(request["spaces"])
    no_account = set(request.get("no_account") or ())   # servers with accounts, none of them this profile's
    return Scope(plugins=tuple(sorted(p for p, m in plugins if placement.plugin_space(p, m) in visible)),
                 tools=tuple(sorted(t for t in tools if placement.tool_space(t) in visible
                                    and t.split("__", 1)[0] not in no_account)),
                 call=request.get("call") or "toolbox__call_tool")


class Spaces:
    def __init__(self, doc: dict):
        self.operator = doc.get("operator") or ""
        spaces = doc.get("spaces") or {}
        if not self.operator:
            raise SpacesError("operator is required")
        if self.operator not in spaces:
            raise SpacesError(f"operator {self.operator!r} needs a space of the same name")
        members = {name: set(s.get("members") or ()) for name, s in spaces.items()}
        for name, m in members.items():
            if not m:
                raise SpacesError(f"space {name!r} has no members")
        self.placement = Placement(self.operator, spaces)
        self.profiles: dict[str, Profile] = {}
        for pname, p in (doc.get("profiles") or {}).items():
            person, wanted = p.get("person"), tuple(p.get("spaces") or ())
            if not wanted:
                raise SpacesError(f"profile {pname!r} has no spaces")
            for sp in wanted:
                if sp not in spaces:
                    raise SpacesError(f"profile {pname!r} names unknown space {sp!r}")
                if person not in members[sp]:
                    raise SpacesError(f"profile {pname!r}: {person!r} is not a member of space {sp!r}")
            defaults = p.get("defaults") or {}
            for server, acct in defaults.items():
                if self.placement.account_space(server, acct) not in wanted:
                    raise SpacesError(f"profile {pname!r}: default account {server}:{acct} is not in its spaces")
            self.profiles[pname] = Profile(pname, person, wanted, self.placement, tuple(sorted(defaults.items())))

    @classmethod
    def single(cls) -> Spaces:
        """No spaces.json: one profile, `default`, that sees everything."""
        self = cls.__new__(cls)
        self.operator, self.placement = "", None
        self.profiles = {"default": Profile("default", "", (), None)}
        return self

    @classmethod
    def load(cls, config_dir: Path) -> Spaces:
        path = config_dir / "spaces.json"
        return cls(json.loads(path.read_text())) if path.exists() else cls.single()


def load_tokens(token_dir: Path, profiles: list[str]) -> dict[str, str]:
    """token -> profile. Each profile's token is `profile-<name>.token`, made on first start and kept,
    so the operator hands it over once. Delete the file to replace a token."""
    token_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in profiles:
        f = token_dir / f"profile-{name}.token"
        if not f.exists():
            f.write_text(secrets.token_urlsafe(32) + "\n")
            f.chmod(0o600)
        out[f.read_text().strip()] = name
    return out
