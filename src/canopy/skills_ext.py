"""The MCP Skills extension (SEP-2640): `skills/list`, `skills/get` and `skill://` resources.

MCPJungle answers these methods with -32601 (checked on v0.4.7), so the catalogue serves them itself
on POST /skills, and the API forwards them at /v1/skills/mcp behind its token. Search stays a Canopy
tool: the extension has none.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import mimetypes
import re
from collections.abc import Callable
from pathlib import Path

import yaml

EXTENSION = "io.modelcontextprotocol/skills"
PAGE = 100
PROTOCOL = "2025-06-18"
NOT_FOUND = -32602
NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")   # the Agent Skills naming rule, 64 characters at most
_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _uri(plugin: str, name: str, rel: str = "SKILL.md") -> str:
    return f"skill://{plugin}/{name}/{rel}"


def _files(folder: Path) -> list[Path]:
    return sorted(f for f in folder.rglob("*") if f.is_file() and not any(
        p.startswith(".") or p == "__pycache__" for p in f.relative_to(folder).parts))


def _json(v):
    """YAML scalars JSON has no type for (dates, timestamps) become the ISO text they were written as."""
    if isinstance(v, dict):
        return {str(k): _json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_json(x) for x in v]
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def strict_frontmatter(text: str, name: str) -> tuple[dict | None, str]:
    """The frontmatter a host will see, or None and why the skill cannot be served under the extension.

    A host parses SKILL.md's YAML itself and refuses the skill if any field differs from the listing, so
    the index's lenient parser (which repairs broken YAML) cannot be used here: only YAML that parses, with
    a `name` and `description`, and a name that obeys the Agent Skills rule and matches the URI."""
    m = _FM.match(text)
    if not m:
        return None, "no YAML frontmatter"
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None, "frontmatter is not valid YAML"
    if not isinstance(fm, dict):
        return None, "frontmatter is not a mapping"
    if not isinstance(fm.get("name"), str) or not isinstance(fm.get("description"), str) or not fm["description"]:
        return None, "frontmatter needs a name and a description"
    if fm["name"] != name or len(name) > 64 or not NAME.fullmatch(name):
        return None, f"name {fm['name']!r} is not a valid Agent Skills name equal to {name!r}"
    return _json(fm), ""


def _entry(plugin: str, name: str, skill_md: Path) -> dict | None:
    folder = skill_md.parent
    fm, _ = strict_frontmatter(skill_md.read_text(errors="replace"), name)
    if fm is None:
        return None
    # Files on disk always have stable digests, so `"dynamic"` is never used. A skill over the limits is
    # still listed in full: the limits bind hosts, which may decline it and say why.
    resources = [{"uri": _uri(plugin, name, f.relative_to(folder).as_posix()),
                  "digest": "sha256:" + hashlib.sha256(f.read_bytes()).hexdigest(),
                  "size": f.stat().st_size} for f in _files(folder)]
    return {"uri": _uri(plugin, name), "frontmatter": fm, "resources": resources}


class Skills:
    """`rows(offset, limit)` gives skill records by id; `one(plugin, name)` gives one or None.
    `root(plugin)` is the plugin's folder in its clone."""

    def __init__(self, rows: Callable, one: Callable, root: Callable[[str], Path]):
        self.rows, self.one, self.root = rows, one, root

    def _locate(self, uri: str) -> tuple[dict, Path, str]:
        parts = uri.removeprefix("skill://").split("/", 2) if uri.startswith("skill://") else []
        if len(parts) < 2:
            raise RpcError(NOT_FOUND, f"unknown skill uri: {uri}")
        row = self.one(parts[0], parts[1])
        if not row or not row["path"]:
            raise RpcError(NOT_FOUND, f"unknown skill uri: {uri}")
        return row, (self.root(row["plugin"]) / row["path"]), parts[2] if len(parts) == 3 else ""

    def list(self, params: dict) -> dict:
        try:
            offset = int(params.get("cursor") or 0)
        except ValueError:
            raise RpcError(NOT_FOUND, "bad cursor") from None
        rows = self.rows(offset, PAGE + 1)
        skills = []
        for r in rows[:PAGE]:
            path = self.root(r["plugin"]) / (r["path"] or "")
            # Left out, as the extension allows a partial listing: a record ahead of its clone, and a skill
            # whose frontmatter a host would refuse (strict_frontmatter). Canopy's `read` still serves both.
            entry = _entry(r["plugin"], r["name"], path) if path.is_file() else None
            if entry:
                skills.append(entry)
        out = {"resultType": "complete", "skills": skills}
        if len(rows) > PAGE:
            out["nextCursor"] = str(offset + PAGE)
        return out

    def get(self, params: dict) -> dict:
        row, path, rel = self._locate(params.get("uri", ""))
        if rel != "SKILL.md" or not path.is_file():
            raise RpcError(NOT_FOUND, f"unknown skill uri: {params.get('uri')}")
        fm, why = strict_frontmatter(path.read_text(errors="replace"), row["name"])
        if fm is None:
            raise RpcError(NOT_FOUND, f"not served as an MCP skill: {why}")
        return {"resultType": "complete", "skill": _entry(row["plugin"], row["name"], path)}

    def read(self, params: dict) -> dict:
        uri = params.get("uri", "")
        _, path, rel = self._locate(uri)
        folder = path.parent.resolve()
        target = (folder / rel).resolve()
        # A uri is user input: nothing outside the skill's own folder may be read.
        if not rel or not target.is_relative_to(folder) or not target.is_file():
            raise RpcError(NOT_FOUND, f"unknown resource: {uri}")
        data = target.read_bytes()
        mime = "text/markdown" if target.suffix == ".md" else mimetypes.guess_type(target.name)[0]
        item = {"uri": uri, "mimeType": mime or "application/octet-stream"}
        try:
            item["text"] = data.decode()
        except UnicodeDecodeError:
            item["blob"] = base64.b64encode(data).decode()
        return {"contents": [item]}

    def handle(self, msg: dict) -> dict | None:
        """One JSON-RPC message in, its reply out; None for a notification."""
        if "id" not in msg:
            return None
        method, params = msg.get("method"), msg.get("params") or {}
        routes = {"skills/list": self.list, "skills/get": self.get, "resources/read": self.read}
        try:
            if method == "initialize":
                result = {"protocolVersion": PROTOCOL, "serverInfo": {"name": "canopy-skills", "version": "1"},
                          "capabilities": {"resources": {}, "extensions": {EXTENSION: {}}}}
            elif method in routes:
                result = routes[method](params)
            else:
                raise RpcError(-32601, f"Method {method} not found")
        except RpcError as e:
            return {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": e.code, "message": str(e)}}
        return {"jsonrpc": "2.0", "id": msg["id"], "result": result}
