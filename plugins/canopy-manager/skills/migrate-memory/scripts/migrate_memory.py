#!/usr/bin/env python3
"""Copy Claude Code auto-memory files into a memory store behind an MCP server.

  migrate_memory.py plan
  migrate_memory.py push   --url URL --namespace NS [--limit N]
  migrate_memory.py verify --url URL --namespace NS

Stdlib only; nothing local is ever changed or deleted. Re-running push stores only what is
missing: each memory's `source` is "claude-auto-memory:<project>/<file>#<hash>", the key.
Tools needed on the server: remember, list_memories (names set with --prefix).
MCP_TOKEN in the environment is sent as a bearer token.
"""
import argparse, hashlib, json, os, pathlib, re, sys, urllib.request

ROOT = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR", pathlib.Path.home() / ".claude")) / "projects"


def front(text):
    m = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.S)
    if not m:
        return {}, text.strip()
    f = {}
    for k in ("name", "description", "type", "modified"):
        x = re.search(rf"^\s*{k}:\s*(.+)$", m[1], re.M)
        if x:
            f[k] = x[1].strip().strip("\"'")
    return f, m[2].strip()


def records(skip):
    seen = set()
    for p in sorted(ROOT.glob("*/memory/**/*.md")):
        rel = p.relative_to(ROOT)
        # MEMORY.md and sub-indexes only list other memories.
        if p.name == "MEMORY.md" or any(re.fullmatch(s, p.name) for s in skip):
            continue
        f, body = front(p.read_text(encoding="utf-8", errors="replace"))
        slug = f.get("name", p.stem)
        head = f"{slug}: {f['description']}" if f.get("description") else slug
        if f.get("modified"):
            head += f" (last updated {f['modified'][:10]})"
        text = f"{head}\n\n{body}".strip()
        h = hashlib.sha256(text.encode()).hexdigest()[:12]
        if h in seen:
            continue  # same memory kept in two project folders
        seen.add(h)
        project = rel.parts[0].strip("-")
        yield {"text": text, "source": f"claude-auto-memory:{rel}#{h}",
               "tags": [f.get("type", "memory"), f"slug:{slug}", f"project:{project}"]}


class MCP:
    def __init__(self, url, prefix):
        self.url, self.prefix, self.sid, self.n = url, prefix, None, 0
        self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                "clientInfo": {"name": "migrate-memory", "version": "1"}})
        self.rpc("notifications/initialized", None, note=True)

    def rpc(self, method, params, note=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not note:
            self.n += 1
            msg["id"] = self.n
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        if os.environ.get("MCP_TOKEN"):
            h["Authorization"] = "Bearer " + os.environ["MCP_TOKEN"]
        with urllib.request.urlopen(urllib.request.Request(self.url, json.dumps(msg).encode(), h), timeout=90) as r:
            self.sid = r.headers.get("Mcp-Session-Id") or self.sid
            raw = r.read().decode()
        if note:
            return None
        if not raw.lstrip().startswith("{"):
            raw = [l[5:] for l in raw.splitlines() if l.startswith("data:")][-1]
        d = json.loads(raw)
        if "error" in d:
            raise RuntimeError(d["error"])
        return d["result"]

    def call(self, tool, **args):
        r = self.rpc("tools/call", {"name": self.prefix + tool, "arguments": args})
        t = "".join(c.get("text", "") for c in r.get("content", []))
        if r.get("isError"):
            raise RuntimeError(t)
        try:
            return json.loads(t)
        except ValueError:
            raise RuntimeError(t[:300])


def stored(mcp, ns):
    out, off = {}, 0
    while True:
        r = mcp.call("list_memories", namespace=ns, limit=100, offset=off)
        ms = r if isinstance(r, list) else r.get("memories", [])
        if not ms:
            return out
        for m in ms:
            out[m.get("source", "")] = m
        off += len(ms)


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("cmd", choices=["plan", "push", "verify"])
    a.add_argument("--url"); a.add_argument("--namespace"); a.add_argument("--prefix", default="memory__")
    a.add_argument("--skip", action="append", default=[r"index-.*\.md"], help="file name regex to leave out")
    a.add_argument("--limit", type=int, default=0)
    o = a.parse_args()
    recs = list(records(o.skip))
    if o.cmd == "plan":
        print(json.dumps({"to_store": len(recs), "chars": sum(len(r["text"]) for r in recs),
                          "longest": max((len(r["text"]) for r in recs), default=0)}))
        return
    if not (o.url and o.namespace):
        sys.exit("--url and --namespace are required")
    mcp = MCP(o.url, o.prefix)
    have = stored(mcp, o.namespace)
    todo = [r for r in recs if r["source"] not in have]
    if o.cmd == "verify":
        print(json.dumps({"expected": len(recs), "found": len(recs) - len(todo),
                          "missing": [r["source"] for r in todo]}, indent=1))
        sys.exit(bool(todo))
    done, unindexed, failed = 0, [], []
    for r in todo[: o.limit or None]:
        try:
            # The source key dedupes; a similarity check would drop distinct look-alikes.
            x = mcp.call("remember", namespace=o.namespace, allow_duplicate=True, **r)
            res = x.get("result")
            if res == "stored":
                done += 1
            elif res == "stored_unindexed":
                unindexed.append(r["source"])
            else:
                failed.append([r["source"], str(x)[:200]])
        except Exception as e:
            failed.append([r["source"], str(e)[:200]])
    print(json.dumps({"stored": done, "unindexed": unindexed, "failed": failed,
                      "already_there": len(recs) - len(todo)}, indent=1))
    sys.exit(bool(failed))


main()
