"""Spaces and profiles: who sees which sources. The design, with a worked household, is docs/spaces.md.

A space owns sources: MCP servers (`servers.json`), marketplaces and context sources (`marketplaces.json`),
each with an optional `"space"` key, so every source is in exactly one space. A profile is a person plus some
of the spaces they are a member of, and a token is issued to a profile. What a profile sees is the union of
its spaces: no overrides, no deny lists, no nesting.

`agent-data/config/spaces.json`:

    {"operator": "daniel",
     "spaces":   {"daniel": {"members": ["daniel"]}, "household": {"members": ["daniel", "hannah"]}, ...},
     "profiles": {"daniel": {"person": "daniel", "spaces": ["daniel", "household"]}, ...}}

A source without `space` belongs to the operator's space, whose id is the operator's name. With no
spaces.json there is one implicit profile that sees everything (`Spaces.single()`).
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path

from .retrieval import Scope

CONTEXT_MARKET = "context"   # as index.CONTEXT_MARKET; repeated so this module needs no database
USER_CONTEXT = "user"        # the volume's own context source, which is always the operator's


class SpacesError(ValueError):
    """The config is inconsistent. The message names the entry."""


@dataclass(frozen=True)
class Profile:
    name: str
    person: str
    spaces: tuple[str, ...]
    scope: Scope | None   # None: sees everything (no spaces.json)


class Spaces:
    def __init__(self, doc: dict, servers: list[dict], marketplaces: dict, call: str = "call_tool"):
        self.operator = doc.get("operator") or ""
        spaces = doc.get("spaces") or {}
        profiles = doc.get("profiles") or {}
        if not self.operator:
            raise SpacesError("operator is required")
        if self.operator not in spaces:
            raise SpacesError(f"operator {self.operator!r} needs a space of the same name")
        members = {name: set(s.get("members") or ()) for name, s in spaces.items()}
        for name, m in members.items():
            if not m:
                raise SpacesError(f"space {name!r} has no members")

        owner: dict[tuple[str, str], str] = {}   # (kind, source) -> space

        def place(kind: str, name: str, space: str | None) -> None:
            space = space or self.operator
            if space not in spaces:
                raise SpacesError(f"{kind} {name!r} is in unknown space {space!r}")
            if (kind, name) in owner:
                raise SpacesError(f"{kind} {name!r} is listed twice")
            owner[(kind, name)] = space

        for s in servers:
            place("server", s["name"], s.get("space"))
        for m in marketplaces.get("marketplaces") or []:
            place("marketplace", m["name"], m.get("space"))
        for c in marketplaces.get("context") or []:
            place("context", c.get("name") or c["repo"].rsplit("/", 1)[-1], c.get("space"))
        owner.setdefault(("context", USER_CONTEXT), self.operator)

        self.profiles: dict[str, Profile] = {}
        for pname, p in profiles.items():
            person, wanted = p.get("person"), tuple(p.get("spaces") or ())
            if not wanted:
                raise SpacesError(f"profile {pname!r} has no spaces")
            for sp in wanted:
                if sp not in spaces:
                    raise SpacesError(f"profile {pname!r} names unknown space {sp!r}")
                if person not in members[sp]:
                    raise SpacesError(f"profile {pname!r}: {person!r} is not a member of space {sp!r}")

            def names(kind: str) -> tuple[str, ...]:
                return tuple(sorted(n for (k, n), sp in owner.items() if k == kind and sp in wanted))
            self.profiles[pname] = Profile(pname, person, wanted, Scope(
                marketplaces=names("marketplace"), plugins=names("context"), servers=names("server"), call=call))
        self.owner = owner

    @classmethod
    def single(cls) -> Spaces:
        """No spaces.json: one profile, `default`, that sees everything."""
        self = cls.__new__(cls)
        self.operator, self.owner = "", {}
        self.profiles = {"default": Profile("default", "", (), None)}
        return self

    @classmethod
    def load(cls, config_dir: Path, servers: list[dict], call: str = "call_tool") -> Spaces:
        path = config_dir / "spaces.json"
        if not path.exists():
            return cls.single()
        mk = config_dir / "marketplaces.json"
        return cls(json.loads(path.read_text()), servers, json.loads(mk.read_text()) if mk.exists() else {}, call)


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
