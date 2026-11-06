#!/usr/bin/env python3
"""Validate a Canopy connection, then benchmark retrieval: reach the gateway, list its tools, find and load real
records, and report the time and tokens each step cost.

Exit 0 means no fault: GREEN or AMBER. Read-only. Standard library only.

    validate.py [--url URL] [--expect TOOL,TOOL] [--probes N] [--queries FILE] [--limit N]
                [--save FILE] [--compare FILE]

The URL defaults to CANOPY_MCP_URL, and the token comes from CANOPY_TOKEN. --probes 0 checks the connection only.

Skills to find are sampled from the server's own catalogue, so this works on any server. Each query comes from the
skill's description, which makes it an easy test: it proves retrieval works, not that real phrasings find the right
skill. For that, pass --queries with a JSON list of {"q": "plain words", "relevant": ["skill:plugin/name", ...]}.

Tokens are characters / 4: an estimate, the same on every run, so runs compare with each other. The tool list is
reported twice, because what a session pays depends on the client: one that defers schemas loads the names, and one
that does not loads every schema.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import statistics
import sys
import time
import urllib.request
from pathlib import Path

TIMEOUT = 30
ALWAYS = ("catalogue__search", "catalogue__read", "catalogue__list", "toolbox__call_tool")
SLOW_MS = 2000
PROBES = 5
QUERY_WORDS = 12
TOOL_CASES = [{"q": "check whether the catalogue index is complete and current", "relevant": ["tool:catalogue__health"]}]


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

    def tool(self, name: str, arguments: dict) -> str:
        result = self.call("tools/call", {"name": name, "arguments": arguments})
        return "".join(c.get("text", "") for c in result["content"])


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


def tokens(text: str) -> int:
    return len(text) // 4


def connect(client: Client) -> None:
    client.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "canopy-validate", "version": "1"}})
    client.post({"jsonrpc": "2.0", "method": "notifications/initialized"})


def found_by_search(client: Client, tool: str) -> bool:
    reply = json.loads(client.tool("catalogue__search", {"query": tool.replace("__", " ").replace("_", " "), "limit": 6}))
    return f"tool:{tool}" in {hit.get("id") for hit in reply.get("tools", []) + reply.get("results", [])}


def phrase(record: dict) -> str:
    """A query for a record: the first sentence of its description, or its name."""
    text = re.split(r"(?<=[.!?])\s", (record.get("description") or "").strip())[0]
    return " ".join(text.split()[:QUERY_WORDS]) or record["name"].replace("-", " ")


def sample(client: Client, count: int) -> list[dict]:
    """Skills spread evenly through the catalogue, the same ones on every run while it holds still."""
    total = json.loads(client.tool("catalogue__list", {"kind": "skill", "limit": 1}))["total"]
    offsets = sorted({(2 * n + 1) * total // (2 * count) for n in range(count)}) if total else []
    records = [json.loads(client.tool("catalogue__list", {"kind": "skill", "limit": 1, "offset": o}))["records"][0]
               for o in offsets]
    return [{"q": phrase(r), "relevant": [r["id"]]} for r in records]


def probe(client: Client, case: dict, limit: int | None) -> dict:
    """Search as an agent would, then load what it found."""
    arguments = {"query": case["q"]} | ({"limit": limit} if limit else {})
    text, ms = timed(client.tool, "catalogue__search", arguments)
    reply = json.loads(text)
    tools_wanted = all(record.startswith("tool:") for record in case["relevant"])
    ids = [hit["id"] for hit in reply.get("tools" if tools_wanted else "results", [])]  # tools rank in their own list
    rank = next((n + 1 for n, record in enumerate(ids) if record in case["relevant"]), None)
    row = {"query": case["q"], "want": case["relevant"], "rank": rank, "ms": ms, "reply_tokens": tokens(text),
           "read_tokens": None, "read_ms": None, "weak": bool(reply.get("weak")), "degraded": bool(reply.get("degraded"))}
    if rank:
        body, row["read_ms"] = timed(client.tool, "catalogue__read", {"ids": [ids[rank - 1]]})
        row["read_tokens"] = tokens(body)
    return row


def summarise(rows: list[dict], listing_tokens: int, names_tokens: int = 0) -> dict:
    found = [row for row in rows if row["rank"]]
    return {"probes": len(rows), "found": len(found), "first": sum(row["rank"] == 1 for row in rows),
            "mrr": round(sum(1 / row["rank"] for row in found) / len(rows), 3) if rows else None,
            "degraded": sum(row["degraded"] for row in rows),
            "search_ms_p50": statistics.median(row["ms"] for row in rows) if rows else None,
            "search_ms_max": max((row["ms"] for row in rows), default=None),
            "reply_tokens_p50": statistics.median(row["reply_tokens"] for row in rows) if rows else None,
            "search_plus_read_tokens_p50":
                statistics.median(row["reply_tokens"] + row["read_tokens"] for row in found) if found else None,
            "tool_list_tokens": listing_tokens, "tool_names_tokens": names_tokens}


def compare(before: dict, after: dict) -> list[str]:
    lines = [f"{key}  {before['summary'].get(key)} -> {value}" for key, value in after["summary"].items()]
    if [row["query"] for row in before["rows"]] != [row["query"] for row in after["rows"]]:
        lines.append("note: the queries differ between these runs, so they do not compare. Use --queries for both.")
    return lines


def verdict(failures: list[str], slowest: int, missed: int = 0, degraded: int = 0, probed: bool = True) -> str:
    if failures:
        return "RED: connection has a fault"
    if missed:
        return f"AMBER: connected, but {missed} retrieval probe(s) missed"
    if degraded:
        return f"AMBER: working, but {degraded} search(es) fell back to keywords only"
    if slowest > SLOW_MS:
        return f"AMBER: working, but slow ({slowest} ms)"
    return f'GREEN: all tests OK, connection {"and retrieval " if probed else ""}working'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=os.environ.get("CANOPY_MCP_URL", ""))
    ap.add_argument("--expect", default="", help="tools to find by search, comma separated")
    ap.add_argument("--probes", type=int, default=PROBES, help="skills to sample; 0 checks the connection only")
    ap.add_argument("--queries", type=Path, help='JSON list of {"q", "relevant"}, used instead of sampling')
    ap.add_argument("--limit", type=int, help="results per search; leave unset for the server's default")
    ap.add_argument("--save", type=Path, help="write this run as JSON")
    ap.add_argument("--compare", type=Path, help="a run saved earlier, to show what moved")
    args = ap.parse_args()
    if not args.url:
        print("RED: no gateway address. Set CANOPY_MCP_URL.")
        return 1

    client = Client(args.url, os.environ.get("CANOPY_TOKEN", ""))
    failures: list[str] = []
    times: list[int] = []
    rows: list[dict] = []
    listing_tokens = names_tokens = 0
    try:
        _, ms = timed(connect, client)
        times.append(ms)
        print(f"ok    reach the gateway  {ms} ms")
        listing, ms = timed(client.call, "tools/list", {})
        times.append(ms)
        names = {tool["name"] for tool in listing["tools"]}
        listing_tokens = tokens(json.dumps(listing["tools"]))
        names_tokens = tokens(" ".join(names))
        missing = [name for name in ALWAYS if name not in names]
        failures += missing
        print(f'{"FAIL" if missing else "ok  "}  list tools ({len(names)}, schemas ~{listing_tokens} tokens, names ~{names_tokens})  {ms} ms'
              + (f'  missing: {", ".join(missing)}' if missing else ""))
        for tool in filter(None, args.expect.split(",")):
            hit, ms = timed(found_by_search, client, tool)
            times.append(ms)
            print(f'{"ok  " if hit else "FAIL"}  find {tool}  {ms} ms')
            if not hit:
                failures.append(tool)
        if args.probes and not missing:
            skills = json.loads(args.queries.read_text()) if args.queries else sample(client, args.probes)
            if not skills:
                failures.append("no skills in the catalogue")
                print("FAIL  the catalogue holds no skills. Run catalogue__health.")
            for case in TOOL_CASES + skills:
                row = probe(client, case, args.limit)
                rows.append(row)
                cost = f'~{row["reply_tokens"]} + ~{row["read_tokens"]} tokens' if row["rank"] else f'~{row["reply_tokens"]} tokens'
                print(f'{"ok  " if row["rank"] else "miss"}  {case["relevant"][0]}  rank {row["rank"]}  {row["ms"]} ms  {cost}')
    except Exception as e:  # any failure is the answer this script exists to give
        failures.append(str(e))
        print(f"FAIL  {type(e).__name__}: {e}")

    summary = summarise(rows, listing_tokens, names_tokens)
    if rows:
        print(f'retrieval  {summary["found"]}/{summary["probes"]} found, {summary["first"]} first, '
              f'median search {summary["search_ms_p50"]} ms, median find and load ~{summary["search_plus_read_tokens_p50"]} tokens')
    run = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "limit": args.limit,
           "summary": summary, "rows": rows}
    if args.compare:
        print("\n".join(compare(json.loads(args.compare.read_text()), run)))
    if args.save:
        args.save.write_text(json.dumps(run, indent=1, ensure_ascii=False) + "\n")
        print(f"saved {args.save}")
    slowest = max(times + [summary["search_ms_p50"] or 0])
    print(verdict(failures, slowest, summary["probes"] - summary["found"], summary["degraded"], bool(rows)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
