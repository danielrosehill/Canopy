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
- Accounts: one server definition can hold several accounts (keys), each in its own space. `call` takes the
  account apart from the tool's arguments; the caller says which accounts it may use. A server that reads its
  key at start gets a session per account, started on first use; one that takes the account as an argument
  (`account_argument`) gets one session, and the hub sets that argument itself.
- A transparent upstream (`"transparent": true`) is another gateway whose tools are already named
  `<server>__<tool>`. They keep their names, so a front door can sit on a gateway it does not replace yet,
  with the same tool names the catalogue indexed from it. A name with no `__` gets the upstream's prefix.
  A server declared here wins its prefix; only names no declared server owns go to the transparent one.
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
    """Every upstream in one place.

    A server entry may carry `accounts`, `{name: overrides}`. Without `account_argument`, each account's
    overrides (`env`, `headers`, `bearer_token`, `url`, ...) are merged into the entry and get their own
    session, `<server>:<account>`. With `account_argument: "<arg>"`, each account is `{"value": ...}` and is
    passed to every call as that argument, over one session."""

    def __init__(self, servers: list[dict], env: dict[str, str] | None = None):
        env = dict(os.environ) if env is None else env
        self.upstreams: dict[str, Upstream] = {}          # session key -> Upstream
        self.accounts: dict[str, dict[str, str]] = {}     # server -> {account: session key or argument value}
        self.account_arg: dict[str, str] = {}             # server -> argument name, for argument-mode servers
        self.description: dict[str, str] = {}
        self.transparent: str | None = None              # the one upstream whose tools keep their own names
        for raw in servers:
            name = raw["name"]
            if SEP in name or ":" in name:
                raise HubError(f"server name {name!r} may not contain {SEP!r} or ':'")
            if name in self.description:
                raise HubError(f"server {name!r} is declared twice")
            self.description[name] = raw.get("description", "")
            if raw.get("transparent"):
                if self.transparent or raw.get("accounts"):
                    raise HubError(f"server {name!r}: only one transparent server, and it takes no accounts")
                self.transparent = name
            base = {k: v for k, v in raw.items() if k not in ("accounts", "account_argument")}
            accounts = raw.get("accounts") or {}
            base.pop("transparent", None)
            if not accounts:
                self._add(name, name, base, env)
            elif raw.get("account_argument"):
                self.account_arg[name] = raw["account_argument"]
                self._add(name, name, base, env)
                self.accounts[name] = {}
                for acct, a in accounts.items():
                    try:
                        self.accounts[name][acct] = expand(a["value"], env)
                    except KeyError as e:
                        self.upstreams[name].error = f"config: account {acct}: {e.args[0]}"
            else:
                self.accounts[name] = {}
                for acct, a in accounts.items():
                    merged = {**base, **a, "env": {**base.get("env", {}), **a.get("env", {})},
                              "headers": {**base.get("headers", {}), **a.get("headers", {})}}
                    self._add(f"{name}:{acct}", name, merged, env)
                    self.accounts[name][acct] = f"{name}:{acct}"

    def _add(self, key: str, server: str, spec: dict, env: dict[str, str]) -> None:
        up = Upstream(server, {}, self.description[server])
        try:
            up.spec = expand(spec, env)
        except KeyError as e:
            up.error = f"config: {e.args[0]}"
        self.upstreams[key] = up

    def _sessions(self, server: str) -> list[Upstream]:
        if server in self.accounts and server not in self.account_arg:
            return [self.upstreams[k] for k in self.accounts[server].values()]
        return [self.upstreams[server]] if server in self.upstreams else []

    def _names(self, name: str, u: Upstream) -> list[tuple[str, str, types.Tool]]:
        """(full name, server it is listed under, tool) for each of an upstream's tools."""
        if name != self.transparent:
            return [(f"{name}{SEP}{t.name}", name, t) for t in u.tools]
        out = []
        for t in u.tools:
            full = t.name if SEP in t.name else f"{name}{SEP}{t.name}"
            server = full.partition(SEP)[0]
            if server == name or server not in self.description:   # a declared server owns its prefix
                out.append((full, server, t))
        return out

    def _lister(self, server: str) -> Upstream | None:
        """The session whose tool list stands for the server: the first one up, else the first."""
        ups = self._sessions(server)
        return next((u for u in ups if u.up), ups[0] if ups else None)

    async def start(self) -> None:
        """Connect every server. A per-account server connects one account, for its tool list; the others
        connect on their first call."""
        firsts = [self._sessions(n)[0] for n in self.description if self._sessions(n)]
        await asyncio.gather(*(u.connect() for u in firsts if u.spec))

    async def stop(self) -> None:
        await asyncio.gather(*(u.close() for u in self.upstreams.values()))

    def servers(self, allow: Allow = None, may_see: Callable[[str], bool] | None = None,
                accounts: Callable[[str, list[str]], list[str]] | None = None) -> list[dict]:
        """Servers with at least one allowed tool, and their allowed tool count. A server whose tools are
        unknown (down) is listed if `may_see(name)` says it could hold an allowed one. A server with accounts
        lists the ones `accounts(server, all)` lets the caller use, and is left out if there are none."""
        out = []
        for name in self.description:
            u = self._lister(name)
            if name == self.transparent and u.up:
                rows: dict[str, int] = {}
                for full, server, _ in self._names(name, u):
                    if allow is None or allow(full):
                        rows[server] = rows.get(server, 0) + 1
                out += [{"name": s, "description": self.description[name] if s == name else f"via {name}",
                         "up": True, "error": None, "tools": n, "listed_at": u.listed_at, "via": name}
                        for s, n in sorted(rows.items())]
                continue
            n = sum(1 for t in u.tools if allow is None or allow(f"{name}{SEP}{t.name}"))
            if not (n or allow is None or (not u.up and (may_see is None or may_see(name)))):
                continue
            row = {"name": name, "description": self.description[name], "up": u.up, "error": u.error,
                   "tools": n, "listed_at": u.listed_at}
            if name in self.accounts:
                usable = self._usable(name, accounts)
                if not usable:
                    continue
                row["accounts"] = usable
            out.append(row)
        return out

    def _usable(self, server: str, accounts: Callable[[str, list[str]], list[str]] | None) -> list[str]:
        every = sorted(self.accounts.get(server, {}))
        return every if accounts is None else sorted(accounts(server, every))

    def tools(self, allow: Allow = None) -> list[dict]:
        """Every allowed tool of every connected server, as `<server>__<tool>`."""
        out = []
        for name in self.description:
            u = self._lister(name)
            if u and u.up:
                out += [{"name": full, "description": t.description or "", "inputSchema": t.inputSchema,
                         "server": server}
                        for full, server, t in self._names(name, u) if allow is None or allow(full)]
        return out

    async def refresh(self, name: str | None = None) -> dict[str, str | None]:
        """Re-list tools, reconnecting a server that is down. Returns each server's error, or None."""
        targets = (self._sessions(name) if name else
                   [u for n in self.description for u in self._sessions(n) if u.up or u is self._sessions(n)[0]])

        async def one(u: Upstream) -> None:
            if not u.spec:
                return
            try:
                await (u.list_tools() if u.up else u.connect())
            except Exception as e:  # noqa: BLE001 — the session died: one reconnect
                u.error = _reason(e)
                await u.connect()
        await asyncio.gather(*(one(u) for u in targets))
        return {n: self._lister(n).error for n in ([name] if name else self.description)}

    async def call(self, name: str, arguments: dict | None = None, allow: Allow = None, account: str | None = None,
                   accounts: Callable[[str, list[str]], list[str]] | None = None,
                   default: Callable[[str], str | None] | None = None) -> types.CallToolResult:
        """Call `<server>__<tool>`. For a server with accounts, `account` picks one of those `accounts(server,
        all)` allows; with none given, `default(server)` if usable, else the only usable one."""
        server, sep, tool = name.partition(SEP)
        if not sep or not tool:
            raise HubError(f"{name!r} is not <server>{SEP}<tool>")
        if allow is not None and not allow(name):
            raise HubError(f"no tool {name!r}")   # not allowed looks the same as absent
        if server not in self.description or server == self.transparent:
            if not self.transparent:
                raise HubError(f"no tool {name!r}")
            return await self._via_transparent(name, tool if server == self.transparent else name,
                                               arguments, account)
        arguments = dict(arguments or {})
        if server in self.accounts:
            usable = self._usable(server, accounts)
            if not usable:
                raise HubError(f"no tool {name!r}")
            if account is None:
                d = default(server) if default else None
                account = d if d in usable else usable[0] if len(usable) == 1 else None
            if account is None:
                raise HubError(f"{server} has several accounts you can use: pass account, one of {usable}")
            if account not in usable:
                raise HubError(f"no account {account!r} on {server}; yours: {usable}")
            if server in self.account_arg:
                arg, value = self.account_arg[server], self.accounts[server][account]
                if arguments.get(arg, value) != value:
                    raise HubError(f"{server}: choose the account with `account`, not `{arg}`")
                arguments[arg] = value
                up = self.upstreams[server]
            else:
                up = self.upstreams[self.accounts[server][account]]
        else:
            if account is not None:
                raise HubError(f"{server} has no accounts")
            up = self.upstreams[server]
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

    async def _via_transparent(self, name: str, tool: str, arguments: dict | None,
                               account: str | None) -> types.CallToolResult:
        """A tool of the transparent upstream, by the name it lists (`tool`). An unknown name is absent."""
        if account is not None:
            raise HubError(f"{name.partition(SEP)[0]} has no accounts")
        up = self.upstreams[self.transparent]
        if not up.up and up.spec:
            await up.connect()
        if not up.up:
            raise HubError(f"{self.transparent} is not connected: {up.error}")
        if not any(full == name for full, _, _ in self._names(self.transparent, up)):
            raise HubError(f"no tool {name!r}")
        try:
            return await self._call(up, tool, arguments)
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            await up.connect()
            if not up.up:
                raise HubError(f"{self.transparent} is not connected: {up.error}") from None
            return await self._call(up, tool, arguments)

    async def _call(self, up: Upstream, tool: str, arguments: dict | None) -> types.CallToolResult:
        try:
            return await asyncio.wait_for(up.session.call_tool(tool, arguments or {}), CALL_TIMEOUT)
        except TimeoutError:
            raise HubError(f"{up.name}{SEP}{tool} gave no answer in {CALL_TIMEOUT:g}s") from None
