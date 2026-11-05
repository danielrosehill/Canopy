#!/usr/bin/env python3
"""Test the connection to the gateway: reach it, list its tools, find known tools by search, and time each step.

Exit 0 is green. Read-only. Standard library only.

    check.py [--url URL] [--expect TOOL,TOOL]

The URL defaults to CANOPY_MCP_URL, and the token comes from CANOPY_TOKEN.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

TIMEOUT = 15
ALWAYS = ("catalogue__search", "catalogue__read", "toolbox__call_tool")
SLOW_MS = 2000


class Client:
    def __init__(self, url: str, token: str):
        self.url, self.token, self.session = url, token, None

    def post(self, body: dict) -> dict | None:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = urllib.request.Request(self.url, json.dumps(body).encode(), headers)
        with urllib.request.urlopen(request, timeout=TIMEOUT) as reply:
            self.session = reply.headers.get("Mcp-Session-Id") or self.session
            return parse(reply.read().decode()) if "id" in body else None

    def call(self, method: str, params: dict) -> dict:
        reply = self.post({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if "error" in reply:
            raise RuntimeError(reply["error"].get("message", "error reply"))
        return reply["result"]


def parse(text: str) -> dict:
    """A reply is plain JSON or server-sent events."""
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])
    return json.loads(text)


def timed(step, *args):
    started = time.perf_counter()
    result = step(*args)
    return result, round((time.perf_counter() - started) * 1000)


def connect(client: Client) -> None:
    client.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "canopy-test", "version": "1"}})
    client.post({"jsonrpc": "2.0", "method": "notifications/initialized"})


def found_by_search(client: Client, tool: str) -> bool:
    result = client.call("tools/call", {"name": "catalogue__search",
                                        "arguments": {"query": tool.replace("__", " ").replace("_", " "), "limit": 6}})
    reply = json.loads("".join(c.get("text", "") for c in result["content"]))
    return f"tool:{tool}" in {hit.get("id") for hit in reply.get("tools", []) + reply.get("results", [])}


def verdict(failures: list[str], slowest: int) -> str:
    if failures:
        return "RED: connection has a fault"
    if slowest > SLOW_MS:
        return f"AMBER: working, but slow ({slowest} ms)"
    return "GREEN: all tests OK, connection working"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=os.environ.get("CANOPY_MCP_URL", ""))
    ap.add_argument("--expect", default="", help="tools to find by search, comma separated")
    args = ap.parse_args()
    if not args.url:
        print("RED: no gateway address. Set CANOPY_MCP_URL.")
        return 1

    client = Client(args.url, os.environ.get("CANOPY_TOKEN", ""))
    failures: list[str] = []
    times: list[int] = []
    try:
        _, ms = timed(connect, client)
        times.append(ms)
        print(f"ok    reach the gateway  {ms} ms")
        listing, ms = timed(client.call, "tools/list", {})
        times.append(ms)
        names = {tool["name"] for tool in listing["tools"]}
        missing = [name for name in ALWAYS if name not in names]
        failures += missing
        print(f'{"FAIL" if missing else "ok  "}  list tools ({len(names)})  {ms} ms' + (f'  missing: {", ".join(missing)}' if missing else ""))
        for tool in filter(None, args.expect.split(",")):
            hit, ms = timed(found_by_search, client, tool)
            times.append(ms)
            print(f'{"ok  " if hit else "FAIL"}  find {tool}  {ms} ms')
            if not hit:
                failures.append(tool)
    except Exception as e:  # any failure is the answer this script exists to give
        failures.append(str(e))
        print(f"FAIL  {type(e).__name__}: {e}")
    print(verdict(failures, max(times, default=0)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
