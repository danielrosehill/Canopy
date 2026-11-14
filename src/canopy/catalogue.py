"""The catalogue MCP server: search and read skills, plugins and tools on demand.

It sits behind the gateway as the `catalogue` upstream.

Record ids:
    plugin:<plugin>
    skill:<plugin>/<name>    command:<plugin>/<name>    agent:<plugin>/<name>
    mcp:<server>             tool:<server>__<tool>

Listing and reading never depend on search. `health` checks the index against the
clones on disk and the manifests, not against sync bookkeeping.
"""
from __future__ import annotations

import collections
import json
import os
import re
import threading
import time

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import gateway as gw
from . import index as ix
from . import ranking
from . import scheduler
from .retrieval import SCOPED, SEARCH_LIMIT, Search, filters as _filters, scope as _scope, summary as _summary
from .skills_ext import Skills

# Reachable only on the stack's internal network; the gateway is the front door.
_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False, allowed_hosts=["*"], allowed_origins=["*"])
mcp = FastMCP("catalogue", transport_security=_security)

SYNC_INTERVAL = scheduler.interval("sync", 3600)   # only to judge overdue: `api` runs the jobs
GATEWAY_INTERVAL = scheduler.interval("gateway-refresh", 900)
EMBED_ON_SYNC = os.environ.get("EMBED_ON_SYNC", "false").lower() == "true"

db = ix.connect()
_sync_lock = threading.Lock()
_embed_lock = threading.Lock()
_job: dict = {"running": False, "started": None, "last": None, "gateway": None, "queued": 0}
_queue: collections.deque = collections.deque()   # sync requests waiting for the worker
_queue_lock = threading.Lock()
_embed_job: dict = {"running": False, "last": None}
_keywords: dict = {"index": None, "hashes": {}}
_keyword_lock = threading.Lock()
searcher = Search(db, lambda: _keywords["index"])


# A scoped catalogue (retrieval.SCOPED) serves only these: the rest report on, or act on, the whole index.
SCOPED_TOOLS = {"search", "read", "list"}


def tool(name: str | None = None):
    """Register a tool to run off the event loop. Measured on a live index on 2026-11-07: a `list` that
    takes 10 ms took 1,555 ms behind a `health`. The function itself stays plain, for tests and callers."""
    def add(fn):
        if not SCOPED or (name or fn.__name__) in SCOPED_TOOLS:
            mcp.tool(name=name)(gw.offload(fn))
        return fn
    return add


def _in_scope(rid: str) -> bool:
    """Whether a scoped catalogue may show this record (always true unscoped)."""
    sc = _scope()
    if not sc:
        return True
    return db.execute(f"SELECT 1 FROM records r WHERE r.id = %s AND {sc[0]}", [rid, *sc[1]]).fetchone() is not None


# --- sync scheduling -------------------------------------------------------------

def _request_sync(only: str | None = None, force: bool = False, mode: str = "full") -> bool:
    """Queue a pass, and start the worker if none is running. Returns whether one was running already.

    Requests wait in order, and one identical to a request still waiting is dropped, so a busy stack
    neither loses a request nor runs the same pass twice. The queue and the `running` flag change
    together under `_queue_lock`, so a request cannot land after the worker's last look and be left."""
    with _queue_lock:
        req = (only, force, mode)
        if req not in _queue:
            _queue.append(req)
        _job["queued"] = len(_queue) if _job["running"] else 0
        if _job["running"]:
            return True
        _job["running"] = True
    threading.Thread(target=_sync_worker, daemon=True).start()
    return False


def _sync_worker() -> None:
    while True:
        with _queue_lock:
            if not _queue:
                _job.update(running=False, queued=0)
                break
            only, force, mode = _queue.popleft()
            _job["queued"] = len(_queue)
        with _sync_lock:
            _run_pass(only, force, mode)
    if EMBED_ON_SYNC and ix.EMBED_URL:
        _kick_embed()   # after the lock: a long first embed must not hold up the next sync


def _run_pass(only: str | None, force: bool, mode: str) -> None:
    """`mode` is quick, full or gateway. A pass that changed nothing leaves the keyword index alone."""
    _job["started"] = time.time()
    try:
        if mode == "gateway":
            _job["gateway"] = ix.refresh_gateway_records(db)
            changed = _job["gateway"]["changed"]
        else:
            _job["last"] = ix.sync(db, only=only, force=force, full=mode == "full")
            changed = _job["last"]["changed"]
    except Exception as e:  # noqa: BLE001 — a crashed pass must show in health
        changed = True
        _job["last"] = {"ok": False, "errors": [f"sync crashed: {e!r}"]}
        with ix.write_lock, db:
            db.execute("INSERT INTO runs (started, finished, ok, summary) VALUES (%s,%s,FALSE,%s)",
                       (_job["started"], time.time(), json.dumps(_job["last"])))
        ix.log(f"sync crashed: {e!r}")
    if changed:
        refresh_keywords()   # first: a new record is findable by keyword before it has a vector


def refresh_keywords() -> None:
    """Bring the keyword index up to date with the records: the first call builds it, and later
    calls change only what differs, so a new skill costs milliseconds however large the catalogue."""
    _keywords["dirty"] = True
    # A caller that finds the lock held leaves `dirty` set, and the holder goes round again, also
    # after releasing, so a refresh asked for during another is never dropped.
    while _keywords.get("dirty") and _keyword_lock.acquire(blocking=False):
        try:
            while _keywords.pop("dirty", False):
                _refresh_keywords_once()
        finally:
            _keyword_lock.release()


def _refresh_keywords_once() -> None:
    try:
        seen = {r["id"]: r["hash"] for r in db.execute("SELECT id, hash FROM records")}
        columns = "SELECT id, name, description, plugin, body FROM records"
        known = _keywords["hashes"]
        if _keywords["index"] is None:
            _keywords.update(index=ranking.KeywordIndex(db.execute(columns).fetchall()), hashes=seen)
            return
        changed = [i for i, h in seen.items() if i not in known or known[i] != h]
        removed = [i for i in known if i not in seen]
        if changed or removed:
            rows = db.execute(f"{columns} WHERE id = ANY(%s)", (changed,)).fetchall() if changed else []
            _keywords["index"].update(rows, removed)
            _keywords["hashes"] = seen
    except Exception as e:  # noqa: BLE001 - search says so through `degraded`
        ix.log(f"keyword index failed: {e!r}")


# --- tools ------------------------------------------------------------------------

@tool()
def search(query: str, kind: str | None = None, plugin: str | None = None,
           marketplace: str | None = None, limit: int = SEARCH_LIMIT, full: bool = False) -> str:
    """Find the skill AND the gateway tool for a task: is there a skill for this, which tool does it, what is it called.

    Describe the task in plain words. `results` holds skills, commands, agents,
    plugins and MCP servers; `tools` holds the best-matching gateway tools, each with
    how to call it (its native name, or toolbox__call_tool for tools not in your list). Prefer a matching skill: it wraps the tool with the right conventions.
    `results` can also hold the user's standing instructions (kind habit, sop or preference): a matching
    one is a rule for this task, so `read` it and follow it.
    kind: plugin | skill | command | agent | mcp | tool | habit | sop | preference.
    marketplace: a configured marketplace name (`context` for the standing instructions).
    `read` an id for full text; for a tool it returns the input schema.
    Hybrid keyword (BM25) and semantic ranking; `degraded` says if semantic is off.
    `confidence` is how close the best result is, and `weak: true` means nothing strong matched:
    stop looking rather than rewording.
    Results are compact (id + description cut to 300 chars, best first); full=true adds
    kind/plugin/name/score and the whole description.
    """
    return _out(searcher.run(query, kind=kind, plugin=plugin, marketplace=marketplace, limit=limit, full=full))


README_CHARS = 1500
SIDE_FILE_CHARS = 60_000
_roots: dict = {"at": 0.0, "map": {}}


def _out(obj) -> str:
    """Compact JSON text: every result goes straight into the model's context."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _plugin_root(plugin: str):
    """Plugin root inside its clone (manifests re-read at most every 10 minutes)."""
    if time.time() - _roots["at"] > 600:
        try:
            manifest, _, _ = ix.read_manifests(fetch=False)
            _roots.update(at=time.time(), map=manifest)
        except Exception:  # noqa: BLE001
            pass
    return ix.plugin_root(plugin, _roots["map"].get(plugin))


def _skill_files(r) -> list[str]:
    """Other files in a skill's directory (references/, scripts/), as readable ids."""
    if r["kind"] != "skill" or not r["path"]:
        return []
    d = (_plugin_root(r["plugin"]) / r["path"]).parent
    if not d.is_dir():
        return []
    return sorted(f"{r['id']}/{f.relative_to(d).as_posix()}" for f in d.rglob("*")
                  if f.is_file() and f.name != "SKILL.md" and not any(
                      p.startswith(".") or p == "__pycache__" for p in f.relative_to(d).parts))


_MENTION = re.compile(r"(?<![\w/.-])((?:references|data|assets|scripts|templates|docs)/[\w./-]+\.\w+)")


def _mentioned_files(r, exclude_own_dir: bool = False) -> list[str]:
    """Plugin-root files a skill's body names (shared references/ etc.) that exist, as file: ids.
    exclude_own_dir drops files inside the skill's own folder: _skill_files already
    lists those as skill:<plugin>/<name>/... ids."""
    root = _plugin_root(r["plugin"]).resolve()
    skill_dir = (root / r["path"]).parent.resolve()
    out = set()
    for rel in _MENTION.findall(r["body"] or ""):
        for base in (skill_dir, root):
            f = (base / rel).resolve()
            if root in f.parents and f.is_file():
                if not (exclude_own_dir and r["kind"] == "skill" and skill_dir in f.parents):
                    out.add(f"file:{r['plugin']}/{f.relative_to(root).as_posix()}")
                break
    return sorted(out)


def _fm_has_description(r) -> bool:
    """True when the body starts with frontmatter whose text contains the record's
    description verbatim (whitespace-normalised). Anything else keeps the header field."""
    body, desc = r["body"] or "", r["description"] or ""
    if not desc or not body.startswith("---"):
        return False
    end = body.find("\n---", 3)
    if end < 0:
        return False
    fm = body[3:end]
    return "description:" in fm and desc in " ".join(fm.split())


def _plugin_file(rid: str) -> dict | None:
    """file:<plugin>/<path from the plugin root>."""
    m = re.fullmatch(r"file:([^/]+)/(.+)", rid)
    if not m:
        return None
    root = _plugin_root(m[1]).resolve()
    f = (root / m[2]).resolve()
    if not root.is_dir() or root not in f.parents or not f.is_file():
        return {"error": f"no such file in plugin {m[1]}"}
    text = f.read_text(errors="replace")
    return {"content": text[:SIDE_FILE_CHARS] + (
        f"\n[cut at {SIDE_FILE_CHARS} of {len(text)} chars]" if len(text) > SIDE_FILE_CHARS else "")}


def _side_file(rid: str) -> dict | None:
    """skill:<plugin>/<name>/<relative path>: a file next to a skill's SKILL.md."""
    if rid.startswith("file:"):
        return _plugin_file(rid)
    m = re.fullmatch(r"skill:([^/]+)/([^/]+)/(.+)", rid)
    if not m:
        return None
    r = db.execute("SELECT * FROM records WHERE id = %s", (f"skill:{m[1]}/{m[2]}",)).fetchone()
    if not r:
        return None
    d = ((_plugin_root(r["plugin"]) / r["path"]).parent).resolve()
    f = (d / m[3]).resolve()
    if d not in f.parents or not f.is_file():
        return {"error": f"no such file next to {r['id']}; its files: {_skill_files(r)}"}
    text = f.read_text(errors="replace")
    return {"content": text[:SIDE_FILE_CHARS] + (
        f"\n[cut at {SIDE_FILE_CHARS} of {len(text)} chars]" if len(text) > SIDE_FILE_CHARS else "")}


@tool()
def read(ids: list[str], full_readme: bool = False) -> str:
    """Full content of one or more records by id.

    skill/command/agent: the whole markdown file, frontmatter included.
    plugin: manifest data, head commit, the ids of everything it contains, and
    the README cut to its first 1,500 characters (full_readme=true for all of it).
    mcp: where it is registered, which plugins declare it (secrets redacted)
    and which records call its tools.
    tool: full description, input schema and how to call it (replaces describe_tools).
    A skill lists its other files in `files`: its own folder (skill:<plugin>/<name>/references/x.md)
    and plugin-root files its body names (file:<plugin>/references/x.md). Read one by that id.
    Output: one block per id, a compact JSON header line then the markdown body as is.
    The header omits `description` when the body's frontmatter already carries it.
    """
    out = _read(ids, full_readme)
    _log_reads(out)
    return _render(out)


def _log_reads(out: dict) -> None:
    # Never let logging fail a read.
    try:
        now = time.time()
        with ix.write_lock, db:
            for rid, item in out.items():
                db.execute("INSERT INTO reads (id, ts, hit) VALUES (%s,%s,%s)", (rid, now, "error" not in item))
    except Exception as e:  # noqa: BLE001
        ix.log(f"read log failed: {e!r}")


def _render(out: dict) -> str:
    parts = []
    for rid, item in out.items():
        item = dict(item)
        body = item.pop("content", None)
        parts.append(f"=== {rid}\n{_out(item)}" + (f"\n\n{body}" if body else ""))
    return "\n\n".join(parts)


def _owner_id(rid: str) -> str:
    """The record a read id belongs to: itself, or for a side file its skill or plugin."""
    if m := re.fullmatch(r"file:([^/]+)/.+", rid):
        return f"plugin:{m[1]}"
    if m := re.fullmatch(r"skill:([^/]+)/([^/]+)/.+", rid):
        return f"skill:{m[1]}/{m[2]}"
    return rid


def _read(ids: list[str], full_readme: bool = False) -> dict:
    out = {}
    for rid in ids:
        if SCOPED and not _in_scope(_owner_id(rid)):
            out[rid] = {"error": "no such record; use list or search to find the id"}
            continue
        r = db.execute("SELECT * FROM records WHERE id = %s", (rid,)).fetchone()
        if not r:
            side = _side_file(rid)
            out[rid] = side or {"error": "no such record; use list or search to find the id"}
            continue
        item = _summary(r) | {"path": r["path"], "content": r["body"]}
        if r["kind"] == "plugin":
            # READMEs can be long, and most reads of a plugin want head and contains.
            if not full_readme and len(item["content"] or "") > README_CHARS:
                item["content"] = item["content"][:README_CHARS] + (
                    f"\n\n[README truncated at {README_CHARS} of {len(r['body'])} chars; "
                    "pass full_readme=true for all of it]")
            p = db.execute("SELECT * FROM plugins WHERE name = %s", (r["name"],)).fetchone()
            if p:
                item |= {"marketplace": p["marketplace"], "repo": p["repo"], "head": p["head"]}
            item["contains"] = [x["id"] for x in db.execute(
                "SELECT id FROM records WHERE plugin = %s AND kind != 'plugin' ORDER BY id", (r["name"],))]
            item["mcp_declared"] = [x["server"] for x in db.execute(
                "SELECT server FROM mcp_decls WHERE plugin = %s", (r["name"],))]
            item["mcp_referenced"] = sorted({f"{x['route']}/{x['server']}" for x in db.execute(
                "SELECT route, server FROM mcp_refs WHERE plugin = %s", (r["name"],))})
        elif r["kind"] == "tool":
            t = db.execute("SELECT * FROM gateway_tools WHERE name = %s", (r["name"],)).fetchone()
            item["description"] = t["description"] if t else r["description"]
            item["input_schema"] = json.loads(t["schema"]) if t else None
            item["call"] = r["body"].split("\n", 1)[0].removeprefix("call: ")
            item.pop("content"); item.pop("path")
        elif r["kind"] == "mcp":
            srv = _mcp_servers(name=r["name"])["servers"][0]
            if SCOPED:   # declaring and referencing records may be outside the scope
                srv = {k: srv[k] for k in ("name", "on_gateway", "routes")}
            item |= srv
            item.pop("content")
        else:
            item["mcp_referenced"] = sorted({f"{x['route']}/{x['server']}" for x in db.execute(
                "SELECT route, server FROM mcp_refs WHERE record_id = %s", (rid,))})
            files = _skill_files(r) + _mentioned_files(r, exclude_own_dir=True)
            if files:
                item["files"] = files
            if _fm_has_description(r):
                # The body's frontmatter already carries the description.
                item.pop("description", None)
        out[rid] = item
    return out


@tool(name="list")
def list_records(kind: str | None = None, plugin: str | None = None,
                 marketplace: str | None = None, offset: int = 0, limit: int = 500) -> str:
    """Enumerate records without searching. Use it to browse a plugin
    (`plugin="<name>"`), every plugin (`kind="plugin"`), or every MCP server.
    Returns ids, names and descriptions, and `total` for paging."""
    where, args = _filters(kind, plugin, marketplace)
    total = db.execute(f"SELECT COUNT(*) FROM records r WHERE {where}", args).fetchone()[0]
    rows = db.execute(f"SELECT * FROM records r WHERE {where} ORDER BY r.kind, r.id LIMIT %s OFFSET %s",
                      [*args, limit, offset]).fetchall()
    return _out({"total": total, "offset": offset, "records": [_summary(r) for r in rows]})


@tool()
def mcp_servers(name: str | None = None, problems_only: bool = False) -> str:
    """The MCP side of the catalogue: every server that is registered on the
    gateway, declared in a plugin's .mcp.json, or called by name in a record.

    For each one: gateway registration, declaring plugins with their (redacted)
    config, and referencing records. problems_only=True keeps only servers that
    records call through the gateway but that the gateway does not have, and
    references that bypass the gateway for a server only it provides."""
    return _out(_mcp_servers(name, problems_only))


def _mcp_servers(name: str | None = None, problems_only: bool = False) -> dict:
    gw = {r["name"]: r for r in db.execute("SELECT * FROM gateway_servers")}
    names = {r["name"] for r in db.execute("SELECT name FROM records WHERE kind = 'mcp'")}
    if name:
        names &= {name}
    out = []
    for n in sorted(names):
        decl = [{"plugin": d["plugin"], "transport": d["transport"], "config": json.loads(d["config"])}
                for d in db.execute("SELECT * FROM mcp_decls WHERE server = %s", (n,))]
        refs = db.execute("SELECT record_id, route, tool FROM mcp_refs WHERE server = %s ORDER BY record_id",
                          (n,)).fetchall()
        routes = sorted({r["route"] for r in refs})
        problems = []
        if ix.CLIENT_NAME in routes and n not in gw:
            problems.append(f"called via mcp__{ix.CLIENT_NAME}__ but not registered on the gateway")
        if n in gw and not gw[n]["enabled"]:
            problems.append("registered on the gateway but disabled")
        if n in gw and not decl and any(r["route"] != ix.CLIENT_NAME for r in refs):
            problems.append(f"called directly as mcp__{n}__*, but it is only on the gateway "
                            f"(mcp__{ix.CLIENT_NAME}__{n}__*)")
        entry = {"name": n, "on_gateway": n in gw, "routes": routes, "declared_by": decl,
                 "referenced_by": sorted({r["record_id"] for r in refs}),
                 "tools_referenced": sorted({r["tool"] for r in refs}), "problems": problems}
        if not problems_only or problems:
            out.append(entry)
    return {"count": len(out), "servers": out}


@tool()
def health() -> str:
    """Is the catalogue complete and current? `ok` is false on any of: an empty
    index, a failed or overdue sync, a plugin whose record count differs from what
    is in its clone on disk, or a manifest plugin that is missing or in error. Every
    problem is listed. Checks the index itself, not the sync job's bookkeeping."""
    problems, warnings = [], []
    counts = {r["kind"]: r["count"] for r in db.execute("SELECT kind, COUNT(*) FROM records GROUP BY kind")}
    if not counts:
        problems.append("index is empty")

    last = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    last_ok = db.execute("SELECT finished FROM runs WHERE ok ORDER BY id DESC LIMIT 1").fetchone()
    if not last:
        problems.append("no sync has ever run")
    else:
        s = json.loads(last["summary"])
        if not last["ok"]:
            problems.append(f"last sync failed: {s.get('errors', [])[:10]}")
    if not last_ok or time.time() - last_ok[0] > 2 * SYNC_INTERVAL + 600:
        problems.append("no successful sync within two intervals")

    plugins = {r["name"]: r for r in db.execute("SELECT * FROM plugins")}
    try:
        manifest, merr, _ = ix.read_manifests(fetch=False)
    except Exception as e:  # noqa: BLE001
        manifest, merr = {}, [str(e)]
    problems += merr
    for n in manifest:
        if n not in plugins:
            problems.append(f"{n}: in manifest, not indexed")
    indexed = {r["plugin"]: r["count"] for r in db.execute("SELECT plugin, COUNT(*) FROM records GROUP BY plugin")}
    for n, p in plugins.items():
        if p["status"] != "ok":
            problems.append(f"{n}: {p['status']} - {p['error']}")
            continue
        root = ix.plugin_root(n, manifest.get(n))
        if not root.exists():
            problems.append(f"{n}: clone missing on disk")
            continue
        on_disk = ix.records_on_disk(n, root, manifest.get(n))
        if on_disk != indexed.get(n, 0):
            problems.append(f"{n}: {indexed.get(n, 0)} records indexed, {on_disk} in clone")

    pending = db.execute("SELECT COUNT(*) FROM records WHERE embedding IS NULL "
                         "OR embed_model IS DISTINCT FROM %s", (ix.EMBED_MODEL,)).fetchone()[0]
    if pending and ix.EMBED_URL:
        if EMBED_ON_SYNC and not _job["running"] and not _embed_job["running"]:
            problems.append(f"{pending} records have no embedding, though every sync should embed them")
        else:
            warnings.append(f"{pending} records have no embedding (semantic search misses them); "
                            "`canopy embed` shows the cost, and `canopy embed --run` starts it")
    if not ix.EMBED_URL:
        warnings.append("no embedding provider configured: keyword search only")
    gw_at = db.execute("SELECT value FROM meta WHERE key = 'gateway_fetched_at'").fetchone()
    if not gw_at or time.time() - float(gw_at[0]) > 2 * GATEWAY_INTERVAL + 600:
        warnings.append("gateway server list is stale; mcp registration status may be wrong")
    gw_last = db.execute("SELECT value FROM meta WHERE key = 'gateway_refresh'").fetchone()
    if gw_last and json.loads(gw_last[0]).get("errors"):
        problems.append(f"last gateway refresh failed: {json.loads(gw_last[0])['errors'][:5]}")
    if _job["running"]:
        warnings.append("a sync is running now; counts may be mid-update")

    return _out({
        "ok": not problems, "problems": problems, "warnings": warnings,
        "counts": counts, "plugins_indexed": len(plugins), "plugins_in_manifest": len(manifest),
        "last_sync": dict(last) | {"summary": json.loads(last["summary"])} if last else None,
        "sync_running": _job["running"], "embed_model": ix.EMBED_MODEL if ix.EMBED_URL else None,
    })


@tool()
def usage(kind: str | None = "skill", plugin: str | None = None, since_days: float = 90,
          sort: str = "cost", limit: int = 50) -> str:
    """How often each record is read: `read` calls per id.

    kind: skill (default) | command | agent | plugin | mcp | tool | file (reference
    files read by id), or null for all. sort: cost (reads x body tokens, the
    order to slim skills in) | reads | last. Tokens are estimated at chars/3.3.
    """
    since = time.time() - since_days * 86400
    where, args = ["l.ts >= %s", "l.hit"], [since]
    if kind == "file":
        where.append("l.id NOT IN (SELECT id FROM records)")
    elif kind:
        where.append("l.id IN (SELECT id FROM records WHERE kind = %s)"); args.append(kind)
    if plugin:
        where.append("(l.id LIKE %s OR l.id LIKE %s)"); args += [f"%:{plugin}/%", f"%:{plugin}"]
    rows = db.execute(
        f"SELECT l.id, COUNT(*) AS n, MAX(l.ts) AS last, MAX(LENGTH(r.body)) AS chars FROM reads l "
        f"LEFT JOIN records r ON r.id = l.id WHERE {' AND '.join(where)} GROUP BY l.id", args).fetchall()
    items = [{"id": r["id"], "reads": r["n"], "last": time.strftime("%Y-%m-%d", time.gmtime(r["last"])),
              "est_tokens": (r["chars"] or 0) * 10 // 33} for r in rows]
    key = {"cost": lambda x: -x["reads"] * x["est_tokens"], "reads": lambda x: -x["reads"],
           "last": lambda x: x["last"]}.get(sort)
    if not key:
        raise ValueError("sort must be cost, reads or last")
    items.sort(key=key)
    if sort == "last":
        items.reverse()
    first = db.execute("SELECT MIN(ts) FROM reads").fetchone()[0]
    return _out({"logging_since": time.strftime("%Y-%m-%d", time.gmtime(first)) if first else None,
                 "since_days": since_days, "total": len(items), "records": items[:limit]})


@tool()
def sync(plugin: str | None = None, force: bool = False, full: bool = False, gateway: bool = False) -> str:
    """Run a sync now in the background instead of waiting for the next pass.
    No arguments: a quick pass, which reindexes only plugins whose commit or marketplace entry
    moved. full: fetch every marketplace and retry plugins in error. plugin: just that one.
    force: reindex even if the commit has not moved. gateway: refresh the gateway's servers and
    tools instead of the plugins. If a sync is running, this one is queued behind it. Poll
    `health` for the result."""
    mode = "gateway" if gateway else "full" if (full or force) and not plugin else "quick"
    queued = _request_sync(plugin, force, mode)
    return _out({"started": not queued, "queued": queued, "plugin": plugin, "force": force, "mode": mode,
                 "last": _job["gateway"] if gateway else _job["last"]})


def _run_embed() -> None:
    """Embed what is pending. A run asked for during another goes round once more, since records
    written in the meantime were not in the first run's list. A failed run does not go round."""
    _embed_job["again"] = True
    while _embed_job.get("again") and _embed_lock.acquire(blocking=False):
        _embed_job["running"] = True
        try:
            while _embed_job.pop("again", False):
                done, err = ix.embed_pending(db)
                _embed_job["last"] = {"embedded": done, "error": err}
                if err:
                    break
        finally:
            _embed_job["running"] = False
            _embed_lock.release()


def _kick_embed() -> None:
    threading.Thread(target=_run_embed, daemon=True).start()


@tool()
def embed(dry_run: bool = True) -> str:
    """Embed records that have no vector yet. Incremental: unchanged records are
    skipped. dry_run=true (the default) only reports how many records and roughly how
    many tokens a run would process, so its cost can be judged first. dry_run=false
    starts the run in the background; poll this tool for `last`."""
    out = {"pending": ix.pending_estimate(db), "running": _embed_job["running"], "last": _embed_job["last"]}
    if dry_run:
        return _out(out)
    if not ix.EMBED_URL:
        return _out(out | {"started": False, "reason": "EMBED_PROVIDER is not set"})
    already = _embed_job["running"]
    if not already:
        threading.Thread(target=_run_embed, daemon=True).start()
    return _out(out | {"started": not already})


# Text content only: a structured copy of the same result costs extra tokens in clients.
for _t in mcp._tool_manager.list_tools():
    _t.fn_metadata.output_schema = None


def _skill_rows(offset: int, limit: int) -> list[dict]:
    return list(db.execute("SELECT id, plugin, name, path FROM records WHERE kind = 'skill' "
                           "ORDER BY id OFFSET %s LIMIT %s", [offset, limit]))


def _skill_row(plugin: str, name: str) -> dict | None:
    return db.execute("SELECT id, plugin, name, path FROM records WHERE kind = 'skill' "
                      "AND plugin = %s AND name = %s", [plugin, name]).fetchone()


skills = Skills(_skill_rows, _skill_row, _plugin_root)


@mcp.custom_route("/search", methods=["POST"])
async def search_rest(request: Request) -> Response:
    """`POST /v1/search` on the API forwards here: the same pipeline as the `search` tool."""
    try:
        return JSONResponse(await run_in_threadpool(searcher.request, await request.json()))
    except ValueError as e:   # a body that is not JSON is a ValueError too
        return JSONResponse({"error": str(e)}, status_code=400)


@mcp.custom_route("/skills", methods=["POST"])
async def skills_rpc(request: Request) -> Response:
    """The MCP Skills extension, which the gateway cannot proxy. See skills_ext.py."""
    reply = await run_in_threadpool(skills.handle, await request.json())
    return JSONResponse(reply) if reply else Response(status_code=202)


def main() -> None:
    threading.Thread(target=refresh_keywords, daemon=True).start()   # searchable before any sync
    mcp.settings.host = os.environ.get("MCP_HTTP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.environ.get("MCP_HTTP_PORT", "3000"))
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
