"""The front door end to end: a real MCP client per profile, the hub on the HTTP fixture, and a stand-in
catalogue that records the scope it was sent. The household is the one in docs/spaces.md."""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

sys.path.insert(0, str(Path(__file__).parent / "contract"))
import upstream as http_fixture  # noqa: E402

from canopy.front import Front  # noqa: E402
from canopy.hub import Hub  # noqa: E402
from canopy.spaces import Spaces  # noqa: E402

DOC = {"operator": "alex",
       "spaces": {"alex": {"members": ["alex"]},
                  "ridgeline": {"members": ["alex"], "tools": ["invoices__*"]},
                  "sam": {"members": ["sam"]},
                  "willow": {"members": ["sam"], "tools": ["booking__*"]},
                  "household": {"members": ["alex", "sam"], "tools": ["ha__*", "inventory__inventory_a",
                                                                         "letters__whoami"],
                                "accounts": ["letters:family"]}},
       "profiles": {"alex": {"person": "alex", "spaces": ["alex", "ridgeline", "household"]},
                    "alex-work": {"person": "alex", "spaces": ["ridgeline"]},
                    "sam": {"person": "sam", "spaces": ["sam", "willow", "household"]}}}
TOKENS = {"tok-work": "alex-work", "tok-sam": "sam", "tok-alex": "alex"}
STDIO = str(Path(__file__).parent / "fixtures" / "stdio_upstream.py")
sent: list[dict] = []


def fake_catalogue(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    sent.append(body)
    if body["op"] == "skills":   # the catalogue's Skills extension answer, tagged with the scope it was sent
        msg = body["message"]
        return httpx.Response(200, json={"reply": {"jsonrpc": "2.0", "id": msg["id"], "result": {
            "method": msg["method"], "spaces": body["scope"]["spaces"]}}})
    return httpx.Response(200, json={"text": json.dumps({"op": body["op"], "spaces": body["scope"]["spaces"]})})


@pytest.fixture(scope="module")
def front_url():
    up = ThreadingHTTPServer(("127.0.0.1", 0), http_fixture.Handler)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{up.server_port}/mcp"
    servers = [{"name": n, "transport": "streamable_http", "url": f"{base}/{n}"}
               for n in ("invoices", "ha", "booking", "inventory")]
    servers.append({"name": "letters", "transport": "stdio", "command": sys.executable, "args": [STDIO],
                    "accounts": {a: {"env": {"ACCOUNT": a}} for a in ("personal", "family")}})
    front = Front(Hub(servers, env={}), Spaces(DOC), TOKENS,
                  catalogue_url="http://catalogue", catalogue_transport=httpx.MockTransport(fake_catalogue))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(front.asgi(), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    up.shutdown()


def as_profile(url: str, token: str, body):
    async def go():
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as client:
            async with streamable_http_client(f"{url}/mcp", http_client=client) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    return await body(session)
    return asyncio.run(go())


def test_everyone_gets_the_same_small_tool_list(front_url):
    async def body(s):
        return sorted(t.name for t in (await s.list_tools()).tools)
    assert as_profile(front_url, "tok-sam", body) == ["call_tool", "list", "list_servers", "read", "search"]


def test_list_servers_counts_only_the_tools_a_profile_has(front_url):
    async def body(s):
        return {x["name"]: x["tools"] for x in
                json.loads((await s.call_tool("list_servers", {})).content[0].text)["servers"]}
    assert as_profile(front_url, "tok-sam", body) == {"ha": 2, "booking": 2, "inventory": 1, "letters": 1}
    assert as_profile(front_url, "tok-work", body) == {"invoices": 2}


def test_one_server_can_share_one_tool_and_keep_the_rest(front_url):
    async def body(s):
        a = await s.call_tool("call_tool", {"name": "inventory__inventory_a"})
        b = await s.call_tool("call_tool", {"name": "inventory__inventory_b"})
        return (a.isError, a.content[0].text), (b.isError, b.content[0].text)
    shared, kept = as_profile(front_url, "tok-sam", body)
    assert shared == (False, "inventory:inventory_a")
    assert kept[0] and "no tool 'inventory__inventory_b'" in kept[1]


def test_a_shared_server_answers_both_and_a_private_one_only_its_owner(front_url):
    async def call(s, name):
        r = await s.call_tool("call_tool", {"name": name})
        return r.isError, r.content[0].text

    async def sam(s):
        return await call(s, "ha__ha_a"), await call(s, "invoices__invoices_a")
    shared, private = as_profile(front_url, "tok-sam", sam)
    assert shared == (False, "ha:ha_a")
    assert private[0] and "no tool 'invoices__invoices_a'" in private[1]

    async def work(s):
        return await call(s, "invoices__invoices_a"), await call(s, "ha__ha_a")
    own, home = as_profile(front_url, "tok-work", work)
    assert own == (False, "invoices:invoices_a")
    assert home[0] and "no tool 'ha__ha_a'" in home[1]   # the work profile leaves the household out


def test_search_reaches_the_catalogue_with_the_profiles_scope(front_url):
    sent.clear()

    async def body(s):
        return json.loads((await s.call_tool("search", {"query": "lights"})).content[0].text)
    assert as_profile(front_url, "tok-sam", body) == {"op": "search", "spaces": ["sam", "willow", "household"]}
    assert sent[-1]["scope"]["call"] == "call_tool" and sent[-1]["args"] == {"query": "lights"}


def test_no_token_or_a_wrong_one_is_refused(front_url):
    init = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    for headers in ({}, {"Authorization": "Bearer nope"}):
        r = httpx.post(f"{front_url}/mcp", json=init, headers={"Accept": "application/json, text/event-stream",
                                                                 **headers})
        assert r.status_code == 401


def test_one_newsletter_server_two_people_each_their_own_accounts(front_url):
    async def call(s, account=None):
        args = {"name": "letters__whoami"} | ({"account": account} if account else {})
        r = await s.call_tool("call_tool", args)
        return r.isError, r.content[0].text

    async def sam(s):
        servers = json.loads((await s.call_tool("list_servers", {})).content[0].text)["servers"]
        return ({x["name"]: x.get("accounts") for x in servers}["letters"],
                await call(s), await call(s, "personal"))
    accounts, own, other = as_profile(front_url, "tok-sam", sam)
    assert accounts == ["family"]
    assert own == (False, "family|")              # her only account: no need to name it
    assert other[0] and "no account 'personal'" in other[1]

    async def alex(s):
        return await call(s), await call(s, "personal"), await call(s, "family")
    unnamed, personal, family = as_profile(front_url, "tok-alex", alex)
    assert unnamed[0] and "several accounts" in unnamed[1]
    assert personal == (False, "personal|") and family == (False, "family|")

    async def work(s):
        servers = json.loads((await s.call_tool("list_servers", {})).content[0].text)["servers"]
        return [x["name"] for x in servers], await call(s, "family")
    names, blocked = as_profile(front_url, "tok-work", work)
    assert "letters" not in names and blocked[0]


def rpc(url: str, token: str, method: str, **params) -> httpx.Response:
    return httpx.post(f"{url}/mcp", json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params},
                      headers={"Accept": "application/json, text/event-stream", "Authorization": f"Bearer {token}"})


def test_initialize_declares_the_skills_extension_and_resources(front_url):
    async def body(s):
        return (await s.initialize()).capabilities.model_dump()
    caps = as_profile(front_url, "tok-sam", body)
    assert caps["extensions"] == {"io.modelcontextprotocol/skills": {}} and caps["resources"] is not None


def test_skills_methods_reach_the_catalogue_with_the_profiles_scope(front_url):
    sent.clear()
    for method, params in (("skills/list", {}), ("skills/get", {"uri": "skill://p/s/SKILL.md"}),
                           ("resources/read", {"uri": "skill://p/s/SKILL.md"})):
        r = rpc(front_url, "tok-work", method, **params)
        assert r.status_code == 200 and r.json() == {"jsonrpc": "2.0", "id": 7, "result": {
            "method": method, "spaces": ["ridgeline"]}}
        assert sent[-1]["message"]["params"] == params


def test_tools_still_go_through_the_sdk_after_a_skills_call(front_url):
    assert rpc(front_url, "tok-sam", "skills/list").status_code == 200

    async def body(s):
        return len((await s.list_tools()).tools)
    assert as_profile(front_url, "tok-sam", body) == 5


def test_skills_calls_need_a_token(front_url):
    assert httpx.post(f"{front_url}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "skills/list"},
                      headers={"Accept": "application/json"}).status_code == 401
