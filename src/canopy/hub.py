"""The hub: Canopy's own MCP aggregator. A prototype, not yet wired into compose.

It replaces MCPJungle's job with Canopy's own model. An MCP server is a first-level source, like a plugin,
and its tools are nested records, like a plugin's skills. The hub is only the plumbing that keeps the servers
connected: one live client session per upstream in `servers.json`, tools named `<server>__<tool>`, and calls
routed by that prefix. Discovery stays in the catalogue, the same for tools and skills.

What it does differently from the MCPJungle registration model (see docs/architecture.md, "Gateway behaviour
the seed depends on"):

- Nothing is registered. The config is the registry, and a server is connected, not stored, so there is no
  "second registration fails" and no delete-then-register.
- Tool lists do not freeze. `refresh` re-lists a server, and a `tools/list_changed` notification does it
  on its own.
- A server that cannot be reached is reported with its error, and the others keep working. A call to it
  tries to connect once more before failing.
- Groups are gone. A caller passes `allow`, a test on each tool name (a profile's, from src/canopy/spaces.py),
  so one server can serve tools to everyone and keep others for its admin. Which tools a client lists
  natively is the front door's choice, not a gateway object to rebuild.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import anyio
import httpx
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

SEP = "__"
CONNECT_TIMEOUT = float(os.environ.get("HUB_CONNECT_TIMEOUT", "30"))
CALL_TIMEOUT = float(os.environ.get("HUB_CALL_TIMEOUT", "120"))
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class HubError(Exception):
    """A call the hub refused or could not route. The message is for the agent."""


def expand(value: object, env: dict[str, str]) -> object:
    """Replace ${VAR} in every string. An unset variable raises: an empty credential fails later and less clearly."""
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            if m[1] not in env:
                raise KeyError(f"${{{m[1]}}} is not set")
            return env[m[1]]
        return _VAR.sub(sub, value)
    if isinstance(value, list):
        return [expand(v, env) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, env) for k, v in value.items()}
    return value


@dataclass
class Upstream:
    """One server from servers.json and its live session. The session lives in its own task, because the
    SDK's transports must be entered and left in the same task."""
    name: str
    spec: dict
    description: str = ""
    session: ClientSession | None = None
    tools: list[types.Tool] = field(default_factory=list)
    error: str | None = None
    listed_at: float = 0.0
    _task: asyncio.Task | None = None
    _stop: asyncio.Event | None = None

    @property
    def up(self) -> bool:
        return self.session is not None

    def _transport(self, stack: contextlib.AsyncExitStack):
        kind = self.spec.get("transport", "stdio")
        if kind == "stdio":
            return stdio_client(StdioServerParameters(
                command=self.spec["command"], args=self.spec.get("args", []),
                env={**os.environ, **self.spec.get("env", {})} if self.spec.get("env") else None))
        if kind == "streamable_http":
            headers = dict(self.spec.get("headers", {}))
            if self.spec.get("bearer_token"):
                headers["Authorization"] = f"Bearer {self.spec['bearer_token']}"
            client = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(CALL_TIMEOUT, connect=CONNECT_TIMEOUT))
            stack.push_async_callback(client.aclose)
            return streamable_http_client(self.spec["url"], http_client=client)
        raise HubError(f"{self.name}: unknown transport {kind!r}")

    async def _on_message(self, msg) -> None:
        if isinstance(msg, types.ServerNotification) and isinstance(msg.root, types.ToolListChangedNotification):
            asyncio.get_running_loop().create_task(self.list_tools())

    async def _hold(self, ready: asyncio.Future) -> None:
        try:
            async with contextlib.AsyncExitStack() as stack:
                streams = await stack.enter_async_context(self._transport(stack))
                session = await stack.enter_async_context(ClientSession(
                    streams[0], streams[1], message_handler=self._on_message))
                await session.initialize()
                self.session = session
                await self.list_tools()
                ready.set_result(None)
                await self._stop.wait()
        except BaseException as e:  # noqa: BLE001 — report it; the rest of the hub keeps going
            self.error = _reason(e)
            if not ready.done():
                ready.set_result(None)
        finally:
            self.session = None

    async def connect(self) -> None:
        await self.close()
        self.error, self._stop = None, asyncio.Event()
        ready = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(self._hold(ready))
        try:
            await asyncio.wait_for(asyncio.shield(ready), CONNECT_TIMEOUT)
        except TimeoutError:
            await self.close()
            self.error = f"no answer in {CONNECT_TIMEOUT:g}s"

    async def close(self) -> None:
        """Leave the session cleanly, or cancel it if it will not go."""
        if self._task:
            self._stop.set()
            try:
                await asyncio.wait_for(asyncio.shield(self._task), 5)
            except BaseException:  # noqa: BLE001 — stuck or already failed: cancel and move on
                self._task.cancel()
                with contextlib.suppress(BaseException):
                    await self._task
        self._task, self.session, self.error = None, None, None

    async def list_tools(self) -> list[types.Tool]:
        if not self.session:
            raise HubError(f"{self.name} is not connected: {self.error or 'not started'}")
        tools, cursor = [], None
        while True:
            page = await self.session.list_tools(cursor)
            tools += page.tools
            cursor = page.nextCursor
            if not cursor:
                break
        self.tools, self.listed_at = tools, time.time()
        return tools


def _reason(e: BaseException) -> str:
    """The real cause, out of the exception groups anyio wraps it in."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__


Allow = Callable[[str], bool] | None   # a test on `<server>__<tool>`; None allows everything


class Hub:
    """Every upstream in one place."""

    def __init__(self, servers: list[dict], env: dict[str, str] | None = None):
        env = dict(os.environ) if env is None else env
        self.upstreams: dict[str, Upstream] = {}
        for raw in servers:
            name = raw["name"]
            if SEP in name:
                raise HubError(f"server name {name!r} may not contain {SEP!r}")
            if name in self.upstreams:
                raise HubError(f"server {name!r} is declared twice")
            up = Upstream(name, {}, raw.get("description", ""))
            try:
                up.spec = expand(raw, env)
            except KeyError as e:
                up.error = f"config: {e.args[0]}"
            self.upstreams[name] = up

    async def start(self) -> None:
        await asyncio.gather(*(u.connect() for u in self.upstreams.values() if u.spec))

    async def stop(self) -> None:
        await asyncio.gather(*(u.close() for u in self.upstreams.values()))

    def servers(self, allow: Allow = None, may_see: Callable[[str], bool] | None = None) -> list[dict]:
        """Servers with at least one allowed tool, and their allowed tool count. A server whose tools are
        unknown (down) is listed if `may_see(name)` says it could hold an allowed one."""
        out = []
        for u in self.upstreams.values():
            n = sum(1 for t in u.tools if allow is None or allow(f"{u.name}{SEP}{t.name}"))
            if n or (not u.up and (may_see is None or may_see(u.name))) or (allow is None):
                out.append({"name": u.name, "description": u.description, "up": u.up, "error": u.error,
                            "tools": n, "listed_at": u.listed_at})
        return out

    def tools(self, allow: Allow = None) -> list[dict]:
        """Every allowed tool of every connected server, as `<server>__<tool>`."""
        return [{"name": f"{u.name}{SEP}{t.name}", "description": t.description or "",
                 "inputSchema": t.inputSchema, "server": u.name}
                for u in self.upstreams.values() if u.up for t in u.tools
                if allow is None or allow(f"{u.name}{SEP}{t.name}")]

    async def refresh(self, name: str | None = None) -> dict[str, str | None]:
        """Re-list tools, reconnecting a server that is down. Returns each server's error, or None."""
        targets = [self.upstreams[name]] if name else list(self.upstreams.values())

        async def one(u: Upstream) -> None:
            if not u.spec:
                return
            try:
                await (u.list_tools() if u.up else u.connect())
            except Exception as e:  # noqa: BLE001 — the session died: one reconnect
                u.error = _reason(e)
                await u.connect()
        await asyncio.gather(*(one(u) for u in targets))
        return {u.name: u.error for u in targets}

    async def call(self, name: str, arguments: dict | None = None, allow: Allow = None) -> types.CallToolResult:
        server, sep, tool = name.partition(SEP)
        if not sep or not tool:
            raise HubError(f"{name!r} is not <server>{SEP}<tool>")
        up = self.upstreams.get(server)
        if up is None or (allow is not None and not allow(name)):
            raise HubError(f"no tool {name!r}")   # not allowed looks the same as absent
        if not up.up and up.spec:
            await up.connect()
        if not up.up:
            raise HubError(f"{server} is not connected: {up.error}")
        try:
            return await self._call(up, tool, arguments)
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            # The session's stream was already closed, so the request was never sent and a retry cannot
            # run the tool twice. This is how a dead stdio server shows up.
            await up.connect()
            if not up.up:
                raise HubError(f"{server} is not connected: {up.error}") from None
            return await self._call(up, tool, arguments)

    async def _call(self, up: Upstream, tool: str, arguments: dict | None) -> types.CallToolResult:
        try:
            return await asyncio.wait_for(up.session.call_tool(tool, arguments or {}), CALL_TIMEOUT)
        except TimeoutError:
            raise HubError(f"{up.name}{SEP}{tool} gave no answer in {CALL_TIMEOUT:g}s") from None
