"""The front door: one MCP endpoint for every person, scoped by profile. Compose service `front` (profile `front`).

A client sends `Authorization: Bearer <token>`; the token names a profile (src/canopy/spaces.py,
docs/spaces.md), and the profile is the scope of everything in the reply. The client's native tool list is
the same small set for everyone, so skills and tools are found the same way:

    search, read, list   the catalogue, as this profile sees it (POST <catalogue>/scoped)
    list_servers         the MCP servers in scope, up or down
    call_tool            any tool in scope, through the hub (src/canopy/hub.py)

Out of scope looks the same as absent, in every one of them.

It also serves the MCP Skills extension (SEP-2640, src/canopy/skills_ext.py) to hosts that support it:
`initialize` declares `io.modelcontextprotocol/skills`, and `skills/list`, `skills/get` and `resources/read`
of a `skill://` URI go to the catalogue with the profile's scope. MCP SDK 1.30 rejects methods it does not
know, so these are answered before the SDK sees the request.

Run: `python -m canopy.front`. Settings: CANOPY_CONFIG (servers.json, marketplaces.json, spaces.json),
FRONT_SERVERS (the hub's server list, default CANOPY_CONFIG/servers.json; a file with one `"transparent": true`
entry fronts an existing gateway, docs/connect/saas-and-web-clients.md), CANOPY_TOKEN_DIR (profile-<name>.token),
CATALOGUE_URL, FRONT_HOST, FRONT_PORT.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

import httpx
import uvicorn
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .hub import Hub, HubError
from .skills_ext import EXTENSION
from .spaces import Profile, Spaces, load_tokens

CONFIG = Path(os.environ.get("CANOPY_CONFIG", "/config"))
TOKEN_DIR = Path(os.environ.get("CANOPY_TOKEN_DIR", "/tokens"))
CATALOGUE_URL = os.environ.get("CATALOGUE_URL", "http://catalogue:3000")
CALL = "call_tool"
SKILL_METHODS = ("skills/list", "skills/get")
PROFILE_KEY = "canopy.profile"

INSTRUCTIONS = """Canopy: your skills, plugins, notes and tools in one place, found by search.
Before a task, `search` it in plain words. A matching skill or standing instruction: `read` it and follow it.
A tool: `read` its id for the schema, then `call_tool`. Everything you can see is yours to use."""

_obj = {"type": "object"}
TOOLS = [
    types.Tool(name="search", description=(
        "Find the skill, standing instruction, note or tool for a task. Describe the task in plain words. "
        "`results` holds skills, plugins, MCP servers and the user's habits, SOPs and preferences; `tools` "
        "holds matching tools. `weak: true` means nothing strong matched: stop rather than rewording."),
        inputSchema={**_obj, "required": ["query"], "properties": {
            "query": {"type": "string"},
            "kind": {"type": "string", "description": "plugin | skill | command | agent | mcp | tool | habit | "
                     "sop | preference | context"},
            "limit": {"type": "integer"}}}),
    types.Tool(name="read", description=(
        "Full text of records by id: a skill's instructions and files, a tool's input schema, "
        "a server's details."),
        inputSchema={**_obj, "required": ["ids"], "properties": {
            "ids": {"type": "array", "items": {"type": "string"}}}}),
    types.Tool(name="list", description="Enumerate records without searching: a plugin's skills, every plugin, "
               "every MCP server or tool. Returns ids, names, descriptions and `total` for paging.",
               inputSchema={**_obj, "properties": {
                   "kind": {"type": "string"}, "plugin": {"type": "string"}, "marketplace": {"type": "string"},
                   "offset": {"type": "integer"}, "limit": {"type": "integer"}}}),
    types.Tool(name="list_servers", description="The MCP servers you can reach, whether each is up, and its "
               "tool count.", inputSchema={**_obj, "properties": {}}),
    types.Tool(name=CALL, description="Call any tool you can reach, by its `<server>__<tool>` name, with an "
               "arguments object matching its schema (`read` the tool's id for it). A server with several "
               "accounts (newsletters, mailboxes) takes `account`; `list_servers` shows yours, and with only "
               "one, or a default, you can leave it out.",
               inputSchema={**_obj, "required": ["name"], "properties": {
                   "name": {"type": "string"}, "arguments": {"type": "object"},
                   "account": {"type": "string"}}}),
]


def _text(s: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=s)]


class _Server(Server):
    """The SDK server, declaring the Skills extension. ServerCapabilities allows extra fields."""

    def get_capabilities(self, *args, **kwargs) -> types.ServerCapabilities:
        caps = super().get_capabilities(*args, **kwargs)
        caps.extensions = {EXTENSION: {}}   # type: ignore[attr-defined]
        return caps


def _is_skill_call(msg) -> bool:
    if not isinstance(msg, dict) or "id" not in msg:
        return False
    if msg.get("method") in SKILL_METHODS:
        return True
    uri = (msg.get("params") or {}).get("uri")
    return msg.get("method") == "resources/read" and isinstance(uri, str) and uri.startswith("skill://")


class Front:
    def __init__(self, hub: Hub, spaces: Spaces, tokens: dict[str, str], catalogue_url: str = CATALOGUE_URL,
                 catalogue_transport: httpx.AsyncBaseTransport | None = None):
        self.hub, self.spaces, self.tokens = hub, spaces, tokens
        self.catalogue = httpx.AsyncClient(base_url=catalogue_url, timeout=60, transport=catalogue_transport)
        self.server = _Server("canopy", instructions=INSTRUCTIONS)
        self.server.list_tools()(self._list_tools)
        # The extension requires the resources capability. Skill files need not be in resources/list.
        self.server.list_resources()(self._list_resources)
        self.server.call_tool(validate_input=False)(self._call_tool)
        self.sessions = StreamableHTTPSessionManager(self.server, stateless=True, json_response=True)

    def _profile(self) -> Profile:
        return self.server.request_context.request.scope[PROFILE_KEY]

    async def _list_tools(self) -> list[types.Tool]:
        return TOOLS

    async def _list_resources(self) -> list[types.Resource]:
        return []

    async def _scoped(self, profile: Profile, body: dict) -> dict:
        # No spaces.json: scope None, which the catalogue takes as "everything".
        req = profile.scope_request(CALL)
        if req is not None:
            req["no_account"] = [s for s, acc in self.hub.accounts.items()
                                 if not profile.accounts_for(s, sorted(acc))]
        r = await self.catalogue.post("/scoped", json={**body, "scope": req})
        doc = r.json()
        if r.status_code != 200:
            raise HubError(f"catalogue: {doc.get('error', r.status_code)}")
        return doc

    async def _catalogue(self, profile: Profile, op: str, args: dict) -> list[types.TextContent]:
        return _text((await self._scoped(profile, {"op": op, "args": args}))["text"])

    async def skill_reply(self, profile: Profile, msg: dict) -> dict:
        """One Skills extension request, answered by the catalogue as this profile sees it."""
        try:
            return (await self._scoped(profile, {"op": "skills", "message": msg}))["reply"]
        except (HubError, httpx.HTTPError, ValueError) as e:
            return {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": str(e)}}

    async def _call_tool(self, name: str, arguments: dict) -> list | types.CallToolResult:
        profile = self._profile()
        allow = profile.allows_tool if profile.placement else None
        try:
            if name in ("search", "read", "list"):
                return await self._catalogue(profile, name, arguments)
            if name == "list_servers":
                return _text(json.dumps({"servers": self.hub.servers(
                    allow, profile.may_see_server, profile.accounts_for)}, indent=1))
            if name == CALL:
                return await self.hub.call(arguments.get("name", ""), arguments.get("arguments"), allow=allow,
                                           account=arguments.get("account"), accounts=profile.accounts_for,
                                           default=profile.default_account)
            raise HubError(f"unknown tool {name!r}")
        except HubError as e:
            return types.CallToolResult(content=_text(f"error: {e}"), isError=True)

    def asgi(self):
        """The MCP endpoint at /mcp behind bearer auth, plus /health. The token sets the profile."""
        sessions, tokens, spaces, front = self.sessions, self.tokens, self.spaces, self

        class McpApp:   # a class, so Starlette's Route passes it the raw ASGI call
            async def __call__(self, scope, receive, send):
                await mcp_app(scope, receive, send)

        async def mcp_app(scope, receive, send):
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode()
            profile = tokens.get(auth.removeprefix("Bearer ").strip()) if auth.startswith("Bearer ") else None
            if profile is None:
                await JSONResponse({"error": "a valid bearer token is required"}, status_code=401)(
                    scope, receive, send)
                return
            scope[PROFILE_KEY] = spaces.profiles[profile]
            if scope.get("method") == "POST":
                body, more = b"", True
                while more:
                    m = await receive()
                    body, more = body + m.get("body", b""), m.get("more_body", False)
                try:
                    msg = json.loads(body)
                except ValueError:
                    msg = None
                if _is_skill_call(msg):
                    await JSONResponse(await front.skill_reply(scope[PROFILE_KEY], msg))(scope, receive, send)
                    return
                sent = False

                async def replay():   # hand the body already read to the SDK
                    nonlocal sent
                    if sent:
                        return await receive()
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                await sessions.handle_request(scope, replay, send)
                return
            await sessions.handle_request(scope, receive, send)

        async def health(_: Request) -> JSONResponse:
            return JSONResponse({"servers": self.hub.servers()})

        @contextlib.asynccontextmanager
        async def lifespan(_app):
            await self.hub.start()
            async with sessions.run():
                yield
            await self.hub.stop()
            await self.catalogue.aclose()

        return Starlette(routes=[Route("/health", health), Route("/mcp", McpApp(), methods=["GET", "POST", "DELETE"])], lifespan=lifespan)


def build() -> Front:
    path = Path(os.environ.get("FRONT_SERVERS") or CONFIG / "servers.json")
    servers = json.loads(path.read_text()).get("servers", [])
    spaces = Spaces.load(CONFIG)
    return Front(Hub(servers), spaces, load_tokens(TOKEN_DIR, list(spaces.profiles)))


def main() -> None:
    uvicorn.run(build().asgi(), host=os.environ.get("FRONT_HOST", "0.0.0.0"),
                port=int(os.environ.get("FRONT_PORT", "8070")))


if __name__ == "__main__":
    main()
