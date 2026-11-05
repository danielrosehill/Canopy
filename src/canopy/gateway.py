"""Client for the MCPJungle gateway: its REST API and its MCP endpoints.

The gateway has two kinds of identity, and neither can do the other's job:

- an MCP client token works on MCP endpoints and gets 401 on the REST API;
- a user token reads the REST API and gets 401 on MCP endpoints.

The seed service writes one of each into the token directory. Tokens are read on
every use, because services start before the seed has created them.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import httpx

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://gateway:8080").rstrip("/")
TOKEN_DIR = Path(os.environ.get("CANOPY_TOKEN_DIR", "/tokens"))
SERVICES_TOKEN = "services"   # MCP client identity
READER_TOKEN = "reader"       # read-only REST identity


def read_token(name: str) -> str:
    try:
        return (TOKEN_DIR / name).read_text().strip()
    except OSError:
        return ""


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def api_get(path: str, timeout: float = 30) -> list | dict:
    r = httpx.get(f"{GATEWAY_URL}/api/v0/{path.lstrip('/')}",
                  headers=_auth(read_token(READER_TOKEN)), timeout=timeout)
    r.raise_for_status()
    return r.json()


def parse_mcp_response(text: str) -> dict | None:
    """A streamable-HTTP reply is plain JSON or an SSE stream with `data:` lines."""
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])
    return json.loads(text) if text.strip() else None


class McpClient:
    """A small, thread-safe MCP client. Only the handshake holds the lock, so one
    slow tool call does not block the others."""

    def __init__(self, url: str, token_name: str = SERVICES_TOKEN, timeout: float = 120,
                 name: str = "canopy"):
        self.url, self.token_name, self.name = url, token_name, name
        self._http = httpx.Client(timeout=timeout)
        self._lock = threading.Lock()
        self._session: str | None = None
        self._next_id = 0

    def _post(self, body: dict, session: str | None) -> httpx.Response:
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   **_auth(read_token(self.token_name))}
        if session:
            headers["Mcp-Session-Id"] = session
        return self._http.post(self.url, json=body, headers=headers)

    def _connect(self) -> str | None:
        with self._lock:
            if self._session:
                return self._session
            r = self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": self.name, "version": "1"}}}, None)
            r.raise_for_status()
            session = r.headers.get("Mcp-Session-Id")
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
            self._session = session
            return session

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        body = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        r = self._post(body, self._connect())
        if r.status_code in (400, 401, 404):   # the session expired: reconnect once
            self._session = None
            r = self._post(body, self._connect())
        r.raise_for_status()
        reply = parse_mcp_response(r.text) or {}
        if "error" in reply:
            raise RuntimeError(f"gateway error: {reply['error']}")
        return reply.get("result", {})

    def tools(self) -> list[dict]:
        return self.request("tools/list").get("tools", [])

    def call(self, name: str, arguments: dict | None = None) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})
