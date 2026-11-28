#!/usr/bin/env python3
"""Probe a Canopy front door as one profile: reach, retrieval accuracy, use. Stdlib only.

    front_probe.py --url http://HOST:8070/mcp --token-file PATH --cases cases.json [--use] [--json]

cases.json:
    {"cases": [{"query": "print a custom text label",
                "expect": ["skill:label-printer/print-label", "tool:print__print_label"],
                "k": 5}],
     "use": [{"name": "print__printers", "arguments": {}}]}

- reach: initialize declares the Skills extension, tools/list is the five front-door tools, skills/list answers.
- retrieval: each case's `search`; a case passes when every `expect` id is in the top `k` of results + tools.
  The hit rate is the profile's retrieval accuracy, the baseline a hosted client should match.
- use (only with --use): each `use` call goes through `call_tool`. Keep them harmless (read-only) unless the
  user asked for a real action.

Exit 0 when every check passed. The report is JSON with --json, else a short table.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

FIVE = {"search", "read", "list", "list_servers", "call_tool"}


class Front:
    def __init__(self, url: str, token: str):
        self.url, self.token, self.n = url, token, 0

    def rpc(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}).encode()
        req = urllib.request.Request(self.url, body, {
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"})
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode()
        if raw.lstrip().startswith("event:") or raw.lstrip().startswith("data:"):
            raw = next(l[5:] for l in raw.splitlines() if l.startswith("data:"))
        return json.loads(raw)

    def tool(self, name: str, arguments: dict) -> tuple[str, bool]:
        res = self.rpc("tools/call", {"name": name, "arguments": arguments})
        if "error" in res:
            return json.dumps(res["error"]), True
        r = res["result"]
        return (r["content"][0]["text"] if r.get("content") else ""), bool(r.get("isError"))


def probe(front: Front, cases: dict, use: bool) -> dict:
    out: dict = {"reach": {}, "retrieval": [], "use": []}
    init = front.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "front-probe", "version": "1"}})
    caps = init.get("result", {}).get("capabilities", {})
    tools = {t["name"] for t in front.rpc("tools/list").get("result", {}).get("tools", [])}
    skills = front.rpc("skills/list").get("result", {}).get("skills")
    out["reach"] = {"skills_extension": "io.modelcontextprotocol/skills" in (caps.get("extensions") or {}),
                    "five_tools": tools == FIVE, "tools": sorted(tools),
                    "skills_listed": len(skills) if isinstance(skills, list) else None}
    for c in cases.get("cases", []):
        k = int(c.get("k", 5))
        t0 = time.monotonic()
        text, err = front.tool("search", {"query": c["query"], "limit": k})
        got: list[str] = []
        if not err:
            doc = json.loads(text)
            got = [r["id"] for r in doc.get("results", [])][:k] + [t["id"] for t in doc.get("tools", [])][:k]
        missing = [e for e in c.get("expect", []) if e not in got]
        out["retrieval"].append({"query": c["query"], "pass": not err and not missing, "missing": missing,
                                 "top": got, "seconds": round(time.monotonic() - t0, 2),
                                 "error": text[:200] if err else None})
    if use:
        for u in cases.get("use", []):
            t0 = time.monotonic()
            text, err = front.tool("call_tool", {"name": u["name"], "arguments": u.get("arguments", {})})
            out["use"].append({"name": u["name"], "pass": not err, "seconds": round(time.monotonic() - t0, 2),
                               "result": text[:300]})
    r = out["retrieval"]
    out["summary"] = {
        "reach": out["reach"]["skills_extension"] and out["reach"]["five_tools"],
        "retrieval_hit_rate": round(sum(x["pass"] for x in r) / len(r), 3) if r else None,
        "use": all(x["pass"] for x in out["use"]) if out["use"] else None}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", required=True)
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--cases", required=True)
    ap.add_argument("--use", action="store_true", help="also run the `use` calls")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    token = open(a.token_file).read().strip()
    report = probe(Front(a.url, token), json.load(open(a.cases)), a.use)
    if a.json:
        print(json.dumps(report, indent=1))
    else:
        s, rch = report["summary"], report["reach"]
        print(f"reach: {'PASS' if s['reach'] else 'FAIL'}  tools={rch['tools']}  skills_listed={rch['skills_listed']}")
        for x in report["retrieval"]:
            print(f"  {'PASS' if x['pass'] else 'FAIL'}  {x['seconds']:>5}s  {x['query']!r}"
                  + (f"  missing={x['missing']}" if x["missing"] else "") + (f"  error={x['error']}" if x["error"] else ""))
        print(f"retrieval hit rate: {s['retrieval_hit_rate']}")
        for x in report["use"]:
            print(f"  {'PASS' if x['pass'] else 'FAIL'}  use {x['name']}  {x['seconds']}s  {x['result'][:80]!r}")
    s = report["summary"]
    ok = s["reach"] and (s["retrieval_hit_rate"] in (None, 1.0)) and s["use"] in (None, True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
