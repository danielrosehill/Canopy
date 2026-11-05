"""The toolbox MCP server: reach every gateway tool through four small tools.

A client lists these four instead of every tool schema, and drills down on demand:

    list_servers()              server names, tool counts, descriptions
    list_tools(server, filter)  tool names and one-line descriptions
    describe_tools(names)       full input schemas, only when needed
    call_tool(name, arguments)  proxied through the gateway

It sees what its gateway identity sees, optionally scoped with TOOLS_GROUP, so a
gateway allow-list or group is the way to keep tools out of its reach.
"""
from __future__ import annotations

import functools
import json
import os
import threading
import time

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import EmbeddedResource, ImageContent, TextContent

from . import gateway as gw

TOOLS_GROUP = os.environ.get("TOOLS_GROUP", "")
CACHE_TTL = int(os.environ.get("TOOLBOX_CACHE_TTL", "300"))
CLIENT_NAME = os.environ.get("CANOPY_CLIENT_NAME", "canopy")
CALL_TIMEOUT = float(os.environ.get("TOOLBOX_CALL_TIMEOUT_SEC", "120"))
SELF_PREFIX = "toolbox__"
CLIENT_PREFIX = f"mcp__{CLIENT_NAME}__"   # how clients and skills spell gateway tools

INSTRUCTIONS = f"""Calls any gateway tool that is not in your tool list. Find the tool
with catalogue__search or list_tools(server=..., filter=...); get its schema with
catalogue__read("tool:<name>") or describe_tools; then call_tool(name, arguments). A
skill that names {CLIENT_PREFIX}<server>__<tool> works as call_tool with that name.
Do not guess arguments. A proxied tool may write or delete data; apply the same
approval policy you would if it were exposed directly."""

# Reachable only on the stack's internal network; the gateway is the front door.
_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False, allowed_hosts=["*"], allowed_origins=["*"])
mcp = FastMCP("toolbox", instructions=INSTRUCTIONS, transport_security=_security)

_url = (f"{gw.GATEWAY_URL}/v0/groups/{TOOLS_GROUP}/mcp" if TOOLS_GROUP else f"{gw.GATEWAY_URL}/mcp")
_client = gw.McpClient(_url, timeout=CALL_TIMEOUT, name="canopy-toolbox")
_cache_lock = threading.Lock()
_cache: dict = {"tools": None, "at": 0.0}


def _all_tools() -> list[dict]:
    with _cache_lock:
        if _cache["tools"] is None or time.time() - _cache["at"] >= CACHE_TTL:
            _cache.update(tools=[t for t in _client.tools() if not t["name"].startswith(SELF_PREFIX)],
                          at=time.time())
        return _cache["tools"]


def _server_descriptions() -> dict[str, str]:
    try:
        return {s["name"]: s.get("description", "") for s in gw.api_get("servers")}
    except Exception:  # noqa: BLE001 — descriptions are optional
        return {}


def _offload(fn):
    """Run a blocking tool body in a worker thread, so a slow proxied call cannot hold
    the event loop, including when the gateway calls back into this process."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
    return wrapper


def norm(name: str) -> str:
    return name.removeprefix(CLIENT_PREFIX)


def short(desc: str, limit: int = 140) -> str:
    desc = " ".join((desc or "").split())
    i = desc.find(". ")
    if 0 < i < limit:
        return desc[: i + 1]
    return desc if len(desc) <= limit else desc[: limit - 1] + "…"


@mcp.tool()
@_offload
def list_servers() -> str:
    """List every MCP server behind the gateway: name, tool count and a one-line
    description. Then use list_tools(server) to drill in."""
    tools = _all_tools()
    counts: dict[str, int] = {}
    for t in tools:
        server = t["name"].split("__")[0]
        counts[server] = counts.get(server, 0) + 1
    descs = _server_descriptions()
    lines = [f"{n}\t{c} tools\t{short(descs.get(n, ''))}" for n, c in sorted(counts.items())]
    return f"{len(counts)} servers, {len(tools)} tools total\n" + "\n".join(lines)


@mcp.tool()
@_offload
def list_tools(server: str = "", filter: str = "") -> str:
    """List tools with one-line descriptions. Scope by server name and/or filter by
    keywords, all of which must appear in the name or summary. Full input schemas are
    left out: use describe_tools for the tools you intend to call."""
    tools = _all_tools()
    if server:
        tools = [t for t in tools if t["name"].split("__")[0] == server]
        if not tools:
            return f"no server '{server}': call list_servers() for valid names"
    if filter:
        terms = filter.lower().split()
        tools = [t for t in tools
                 if all(x in f"{t['name']} {short(t.get('description', ''))}".lower() for x in terms)]
    if not tools:
        return "(no matching tools)"
    if len(tools) > 200:
        return f"{len(tools)} tools match, too many to list; narrow with server= or filter="
    return f"{len(tools)} tools\n" + "\n".join(f"{t['name']}\t{short(t.get('description', ''))}" for t in tools)


@mcp.tool()
@_offload
def describe_tools(names: list[str]) -> str:
    """Full definitions (description and JSON input schema) for up to 10 named tools."""
    if len(names) > 10:
        return "error: max 10 tools per call"
    by_name = {t["name"]: t for t in _all_tools()}
    return json.dumps([by_name.get(norm(n)) or {"name": n, "error": "unknown tool"} for n in names], indent=2)


def to_blocks(result: dict) -> list:
    """Pass content blocks through as themselves: an image must reach the client as an
    image, not as base64 text in its context."""
    blocks: list = []
    for c in result.get("content", []):
        kind = c.get("type")
        if kind == "image":
            blocks.append(ImageContent.model_validate(c))
        elif kind == "resource":
            blocks.append(EmbeddedResource.model_validate(c))
        else:
            blocks.append(TextContent(type="text", text=c.get("text") if kind == "text" else json.dumps(c)))
    if not blocks:
        blocks = [TextContent(type="text", text=json.dumps(result.get("structuredContent", result)))]
    if result.get("isError") and isinstance(blocks[0], TextContent):
        blocks[0] = TextContent(type="text", text=f"error: {blocks[0].text}")
    return blocks


@mcp.tool(structured_output=False)
@_offload
def call_tool(name: str, arguments: dict | None = None) -> list:
    """Invoke any gateway tool by name, with a JSON arguments object matching its schema
    (catalogue__read("tool:<name>") or describe_tools)."""
    if norm(name).startswith(SELF_PREFIX):
        return [TextContent(type="text", text="error: toolbox cannot call itself")]
    try:
        return to_blocks(_client.call(norm(name), arguments))
    except Exception as e:  # noqa: BLE001 — the agent needs the reason, not a crash
        return [TextContent(type="text", text=f"error: {e}")]


def main() -> None:
    mcp.settings.host = os.environ.get("MCP_HTTP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.environ.get("MCP_HTTP_PORT", "3000"))
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
