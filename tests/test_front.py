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

DOC = {"operator": "daniel",
       "spaces": {"daniel": {"members": ["daniel"]}, "ridgeline": {"members": ["daniel"]},
                  "hannah": {"members": ["hannah"]}, "willow": {"members": ["hannah"]},
                  "household": {"members": ["daniel", "hannah"]}},
       "profiles": {"daniel-work": {"person": "daniel", "spaces": ["ridgeline"]},
                    "hannah": {"person": "hannah", "spaces": ["hannah", "willow", "household"]}}}
TOKENS = {"tok-work": "daniel-work", "tok-hannah": "hannah"}
sent: list[dict] = []


def fake_catalogue(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    sent.append(body)
    return httpx.Response(200, json={"text": json.dumps({"op": body["op"], "servers": body["scope"]["servers"]})})


@pytest.fixture(scope="module")
def front_url():
    up = ThreadingHTTPServer(("127.0.0.1", 0), http_fixture.Handler)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{up.server_port}/mcp"
    servers = [{"name": "invoices", "space": "ridgeline", "transport": "streamable_http", "url": f"{base}/invoices"},
               {"name": "ha", "space": "household", "transport": "streamable_http", "url": f"{base}/ha"},
               {"name": "booking", "space": "willow", "transport": "streamable_http", "url": f"{base}/booking"}]
    front = Front(Hub(servers, env={}), Spaces(DOC, servers, {}), TOKENS,
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
    assert as_profile(front_url, "tok-hannah", body) == ["call_tool", "list", "list_servers", "read", "search"]


def test_list_servers_shows_only_the_profiles_spaces(front_url):
    async def body(s):
        return {x["name"] for x in json.loads((await s.call_tool("list_servers", {})).content[0].text)["servers"]}
    assert as_profile(front_url, "tok-hannah", body) == {"ha", "booking"}
    assert as_profile(front_url, "tok-work", body) == {"invoices"}


def test_a_shared_server_answers_both_and_a_private_one_only_its_owner(front_url):
    async def call(s, name):
        r = await s.call_tool("call_tool", {"name": name})
        return r.isError, r.content[0].text

    async def hannah(s):
        return await call(s, "ha__ha_a"), await call(s, "invoices__invoices_a")
    shared, private = as_profile(front_url, "tok-hannah", hannah)
    assert shared == (False, "ha:ha_a")
    assert private[0] and "no server 'invoices'" in private[1]

    async def work(s):
        return await call(s, "invoices__invoices_a"), await call(s, "ha__ha_a")
    own, home = as_profile(front_url, "tok-work", work)
    assert own == (False, "invoices:invoices_a")
    assert home[0] and "no server 'ha'" in home[1]   # the work profile leaves the household out


def test_search_reaches_the_catalogue_with_the_profiles_scope(front_url):
    sent.clear()

    async def body(s):
        return json.loads((await s.call_tool("search", {"query": "lights"})).content[0].text)
    assert as_profile(front_url, "tok-hannah", body) == {"op": "search", "servers": ["booking", "ha"]}
    assert sent[-1]["scope"]["call"] == "call_tool" and sent[-1]["args"] == {"query": "lights"}


def test_no_token_or_a_wrong_one_is_refused(front_url):
    init = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    for headers in ({}, {"Authorization": "Bearer nope"}):
        r = httpx.post(f"{front_url}/mcp", json=init, headers={"Accept": "application/json, text/event-stream",
                                                                 **headers})
        assert r.status_code == 401
