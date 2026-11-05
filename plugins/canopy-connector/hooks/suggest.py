#!/usr/bin/env python3
"""UserPromptSubmit hook: name up to 3 skills when the prompt strongly matches one.

Prints nothing unless the server reports a confident match, so most prompts cost no tokens. Any
failure is silent too: a hook must never get in the way of a prompt. Standard library only.

Reads CANOPY_MCP_URL and CANOPY_TOKEN. CANOPY_HOOK=off turns it off.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

TIMEOUT = 2.5          # the hook's own limit in hooks.json is 3 seconds
MIN_WORDS = 3          # "continue", "yes go ahead": nothing to search on
MAX_HITS = 3
LINE_CHARS = 80


def post(url: str, token: str, body: dict, session: str | None) -> tuple[str, str | None]:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(url, json.dumps(body).encode(), headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode(), r.headers.get("Mcp-Session-Id")


def parse(text: str) -> dict:
    """A reply is plain JSON or server-sent events."""
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])
    return json.loads(text)


def search(url: str, token: str, query: str) -> dict:
    _, session = post(url, token, {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "canopy-hook", "version": "1"}}}, None)
    post(url, token, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
    text, _ = post(url, token, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "catalogue__search", "arguments": {"query": query, "limit": MAX_HITS}}}, session)
    content = parse(text)["result"]["content"]
    return json.loads("".join(c.get("text", "") for c in content))


def render(reply: dict) -> str:
    # No confidence means keyword-only search, which cannot say a match is strong.
    if "confidence" not in reply or reply.get("weak"):
        return ""
    lines = []
    for hit in reply.get("results", [])[:MAX_HITS]:
        desc = hit.get("description", "")
        lines.append(f"- {hit['id']}: {desc[:LINE_CHARS - 1] + '…' if len(desc) > LINE_CHARS else desc}")
    return "Canopy skills that may fit (catalogue__read to load):\n" + "\n".join(lines) if lines else ""


def main() -> None:
    url = os.environ.get("CANOPY_MCP_URL")
    if not url or os.environ.get("CANOPY_HOOK") == "off":
        return
    prompt = json.load(sys.stdin).get("prompt", "").strip()
    if prompt.startswith("/") or len(prompt.split()) < MIN_WORDS:
        return
    out = render(search(url, os.environ.get("CANOPY_TOKEN", ""), prompt[:500]))
    if out:
        print(out)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - silent by design, see the docstring
        pass
