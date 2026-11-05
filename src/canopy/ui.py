"""The web UI: fuzzy search over the catalogue for people. Read-only.

Serves one page and a small JSON API over the catalogue, using the services identity.
It has no login of its own, so it must stay on a trusted address. Requests whose Host
header is not in UI_ALLOWED_HOSTS are refused, which stops DNS rebinding from a web page.
"""
from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import gateway as gw

PORT = int(os.environ.get("UI_PORT", "8081"))
ALLOWED = {h.strip().lower() for h in os.environ.get("UI_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")}
CORE_GROUP = os.environ.get("CORE_GROUP", "core")
CACHE_TTL = 300
PAGE = (Path(__file__).parent / "ui.html").read_bytes()

client = gw.McpClient(f"{gw.GATEWAY_URL}/v0/groups/{CORE_GROUP}/mcp", timeout=60, name="canopy-ui")
_cache: dict = {"at": 0.0, "records": []}


def tool_text(name: str, args: dict) -> str:
    result = client.call(name, args)
    return "".join(c.get("text", "") for c in result.get("content", []))


def all_records() -> list[dict]:
    if time.time() - _cache["at"] > CACHE_TTL or not _cache["records"]:
        records, offset = [], 0
        while True:
            page = json.loads(tool_text("catalogue__list", {"offset": offset, "limit": 1000}))
            records += page["records"]
            offset += len(page["records"])
            if not page["records"] or offset >= page["total"]:
                break
        _cache.update(at=time.time(), records=records)
    return _cache["records"]


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, kind: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        host = self.headers.get("Host", "").rsplit(":", 1)[0].lower()
        if host not in ALLOWED:
            return self._send(403, b'{"error":"host not allowed; see UI_ALLOWED_HOSTS"}')
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if url.path == "/api/records":
                return self._send(200, json.dumps(all_records()).encode())
            if url.path == "/api/read" and q.get("id"):
                text = tool_text("catalogue__read", {"ids": [q["id"]]})
                return self._send(200, text.encode(), "text/plain; charset=utf-8")
            if url.path == "/api/search" and q.get("q"):
                text = tool_text("catalogue__search", {"query": q["q"], "limit": 25})
                return self._send(200, text.encode())
            if url.path == "/api/health":
                return self._send(200, tool_text("catalogue__health", {}).encode())
            return self._send(404, b'{"error":"not found"}')
        except Exception as e:  # noqa: BLE001
            return self._send(502, json.dumps({"error": str(e)}).encode())

    def log_message(self, *args) -> None:
        pass


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
