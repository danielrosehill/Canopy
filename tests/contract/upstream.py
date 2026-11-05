"""A tiny MCP upstream for the contract suite. Standard library only, so it runs in a stock
Python image. `/mcp/<name>` serves tools `<name>_a` and `<name>_b`:

- `/mcp/empty*` serves no tools
- `/mcp/slow*` takes SLOW_SEC to answer `tools/list`
- `POST /extra/<name>` with {"tool": "x"} adds a tool to that upstream, without telling anyone
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SLOW_SEC = float(os.environ.get("SLOW_SEC", "4"))
extra: dict[str, list[str]] = defaultdict(list)


def tools(name: str) -> list[dict]:
    names = [] if name.startswith("empty") else [f"{name}_a", f"{name}_b"]
    return [{"name": t, "description": f"Contract fixture tool {t}",
             "inputSchema": {"type": "object", "properties": {}}} for t in names + extra[name]]


def answer(name: str, msg: dict) -> dict:
    method = msg.get("method")
    if method == "initialize":
        return {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": f"contract-{name}", "version": "1"}}
    if method == "tools/list":
        if name.startswith("slow"):
            time.sleep(SLOW_SEC)
        return {"tools": tools(name)}
    if method == "tools/call":
        return {"content": [{"type": "text", "text": f"{name}:{msg['params']['name']}"}]}
    return {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def reply(self, status: int, doc: dict | None = None) -> None:
        body = json.dumps(doc).encode() if doc is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        # Health for compose. `GET /mcp/...` is the optional SSE stream, which we do not offer.
        self.reply(200 if self.path == "/health" else 405, {})

    def do_DELETE(self) -> None:
        self.reply(200, {})

    def do_POST(self) -> None:
        msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        kind, _, name = self.path.strip("/").partition("/")
        if kind == "extra":
            extra[name].append(msg["tool"])
            self.reply(200, {})
        elif "id" not in msg:   # a notification
            self.reply(202)
        else:
            self.reply(200, {"jsonrpc": "2.0", "id": msg["id"], "result": answer(name, msg)})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 3000), Handler).serve_forever()
