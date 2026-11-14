"""The index: marketplaces -> shallow clones -> Postgres.

Records are plugins, the skills, commands and agents inside them, and the MCP
servers and tools known to the gateway. The `catalogue` schema is a cache: everything in
it except `reads` (usage history) comes from git and the gateway, so deleting it and
syncing rebuilds it. Vectors are the costly part, so a record keeps its vector while the
text that was embedded stays the same.

A plugin's head commit is written in the same transaction as its records, so a
failed pass leaves the old head and the next pass retries it.

Marketplaces are other people's content. Manifests, plugin names, refs and paths
are untrusted input: see `safe_name`, `git_url` and `clean_ref`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
import yaml

from . import gateway as gw
from . import store

DATA = Path(os.environ.get("CANOPY_DATA", "/data"))
CONFIG = Path(os.environ.get("CANOPY_CONFIG", "/config"))
GIT_TOKEN = os.environ.get("GITHUB_TOKEN", "")
# Every embedding goes through the API. No provider means keyword search only.
EMBED_URL = (os.environ.get("CANOPY_API_URL", "http://api:8090").rstrip("/") + "/v1"
             if os.environ.get("EMBED_PROVIDER") else "")
API_TOKEN_FILE = Path(os.environ.get("CANOPY_API_DIR", "/api")) / "token"
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")   # the canonical name: see system.model_map
# Tool records come from what this group serves; empty means everything the services
# identity can see. CORE_GROUP is what agents list natively; other tools go via toolbox.
TOOLS_GROUP = os.environ.get("TOOLS_GROUP", "")
CORE_GROUP = os.environ.get("CORE_GROUP", "core")
# The name clients give this gateway, which is the `<client>` in mcp__<client>__<tool>.
CLIENT_NAME = os.environ.get("CANOPY_CLIENT_NAME", "canopy")
BODY_CAP = 200_000   # bytes kept per record
GRAPHQL_BATCH = 50   # repositories per GraphQL head query

write_lock = threading.Lock()


def clones() -> Path:
    return DATA / "clones"


def market_dir() -> Path:
    return DATA / "marketplaces"


def connect() -> store.Db:
    DATA.mkdir(parents=True, exist_ok=True)
    store.migrate()
    return store.Db()


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# --- validation of untrusted names ---------------------------------------------

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_REPO = re.compile(r"[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*")
_REF = re.compile(r"[A-Za-z0-9][\w./-]*")
_GH_URL = re.compile(r"^(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?/?$")


def safe_name(name: object) -> str:
    """Names become directory names, so they may not contain a path."""
    if not isinstance(name, str) or not _NAME.fullmatch(name) or ".." in name:
        raise ValueError(f"unsafe name {name!r}")
    return name


def clean_ref(ref: object) -> str | None:
    """A ref is passed to git as an argument, so it may not look like an option."""
    if ref is None or ref == "":
        return None
    if not isinstance(ref, str) or not _REF.fullmatch(ref) or ".." in ref:
        raise ValueError(f"unsafe ref {ref!r}")
    return ref


def clean_subpath(path: object) -> str:
    p = str(path or "").strip("/")
    if p.startswith("./"):
        p = p[2:]
    if p and (".." in Path(p).parts or p.startswith("-")):
        raise ValueError(f"unsafe path {path!r}")
    return p


def git_url(repo: str) -> str:
    """`owner/repo` means GitHub; anything else must be https. No file:// or ext::."""
    if _REPO.fullmatch(repo):
        cred = f"x-access-token:{GIT_TOKEN}@" if GIT_TOKEN else ""
        return f"https://{cred}github.com/{repo}.git"
    if repo.startswith("https://") and not re.search(r"\s", repo):
        return repo
    raise ValueError(f"unsupported repo {repo!r}: use owner/repo or an https:// URL")


# --- git -----------------------------------------------------------------------

_SHA = re.compile(r"[0-9a-f]{40}")
# Symlinks in a clone become plain files, so a plugin cannot point a SKILL.md at a token.
_GIT = ["git", "-c", "core.symlinks=false", "-c", "protocol.allow=never",
        "-c", "protocol.https.allow=always"]


def _git(*args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    r = subprocess.run([*_GIT, *args], capture_output=True, text=True, timeout=timeout, env=env)
    if r.returncode != 0:
        err = r.stderr.replace(GIT_TOKEN, "***") if GIT_TOKEN else r.stderr
        raise RuntimeError(f"git {args[0]} failed: {err.strip()[:300]}")
    return r


def remote_head(repo: str, ref: str | None = None) -> str:
    """The commit a plugin resolves to: HEAD, or a pinned branch, tag or sha."""
    if ref and _SHA.fullmatch(ref):
        return ref
    # An annotated tag lists the tag object, and `<commit> <ref>^{}` only if asked for by that
    # pattern too. Prefer the commit, which is what GraphQL returns, so the two never disagree.
    pattern = ref or "HEAD"
    out = _git("ls-remote", git_url(repo), pattern, f"{pattern}^{{}}", timeout=60).stdout.split()
    if not out:
        raise RuntimeError(f"ls-remote {repo} {pattern}: empty response")
    pairs = dict(zip(out[1::2], out[0::2]))
    return next((v for k, v in pairs.items() if k.endswith("^{}")), out[0])


def fetch_clone(repo: str, dest: Path, ref: str | None = None) -> None:
    """Shallow fetch and hard reset. Clones are a read-only cache."""
    if not (dest / ".git").exists():
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        _git("init", "-q", str(dest), timeout=60)
    _git("-C", str(dest), "fetch", "-q", "--depth", "1", git_url(repo), ref or "HEAD")
    _git("-C", str(dest), "reset", "-q", "--hard", "FETCH_HEAD", timeout=60)


def plugin_root(name: str, meta: dict | None) -> Path:
    path = (meta or {}).get("path") or ""
    return clones() / name / path if path else clones() / name


# --- extraction ----------------------------------------------------------------

_FM = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)


def frontmatter(text: str) -> tuple[dict, str]:
    m = _FM.match(text)
    if not m:
        return {}, text
    raw, body = m.group(1), text[m.end():]
    try:
        fm = yaml.safe_load(raw)
        if isinstance(fm, dict):
            return fm, body
    except yaml.YAMLError:
        pass
    # Unquoted colons in descriptions break YAML often enough to need a fallback.
    fm = {}
    for line in raw.splitlines():
        k, sep, v = line.partition(":")
        if sep and re.fullmatch(r"[A-Za-z_-]+", k.strip()):
            fm[k.strip()] = v.strip().strip("\"'")
    return fm, body


def _first_para(body: str) -> str:
    for block in re.split(r"\n\s*\n", body):
        block = block.strip()
        if block and not block.startswith("#"):
            return re.sub(r"\s+", " ", block)[:300]
    return ""


def extract(plugin: str, root: Path) -> list[dict]:
    """Records in one plugin: every SKILL.md, and every .md under a `commands/` or
    `agents/` directory. A nested command is named with `:` joining its directories,
    as Claude Code names it. A subdirectory with its own plugin.json is a different
    plugin vendored into this one, and is skipped."""
    out: list[dict] = []
    seen: set[str] = set()
    nested = {pj.parent.parent for pj in root.rglob(".claude-plugin/plugin.json")} - {root}
    for f in sorted(root.rglob("*.md")):
        rel = f.relative_to(root)
        parts = rel.parts
        if any(p.startswith(".") and p != ".claude" for p in parts[:-1]) or "node_modules" in parts:
            continue
        if any(n in f.parents for n in nested):
            continue
        if f.name == "SKILL.md":
            kind, default = "skill", f.parent.name
        elif "commands" in parts[:-1]:
            i = parts.index("commands")
            kind, default = "command", ":".join([*parts[i + 1:-1], f.stem])
        elif "agents" in parts[:-1]:
            kind, default = "agent", f.stem
        else:
            continue
        text = f.read_text(errors="replace")
        fm, body = frontmatter(text)
        name = str(fm.get("name") or default).strip()
        rid = f"{kind}:{plugin}/{name}"
        if rid in seen:   # the same name twice in one plugin: use the path
            rid = f"{kind}:{plugin}/{rel.as_posix()}"
        seen.add(rid)
        out.append({
            "id": rid, "kind": kind, "plugin": plugin, "name": name,
            "description": re.sub(r"\s+", " ", str(fm.get("description") or _first_para(body))).strip(),
            "path": rel.as_posix(), "body": text[:BODY_CAP],
            "hash": hashlib.sha256(text.encode()).hexdigest(),
        })
    return out


_disk_counts: dict[str, tuple[bytes, Path, int]] = {}


def records_on_disk(plugin: str, root: Path) -> int:
    """How many records a clone holds, the plugin's own included. Parsing every clone took 1.6 s on
    293 plugins, and health runs every 5 minutes, so the count is kept until the clone moves.
    FETCH_HEAD is written by the fetch itself, so this follows the clone, not sync's bookkeeping."""
    try:
        fetched = (clones() / plugin / ".git" / "FETCH_HEAD").read_bytes()
    except OSError:
        return len(extract(plugin, root)) + 1
    known = _disk_counts.get(plugin)
    if known and known[:2] == (fetched, root):
        return known[2]
    count = len(extract(plugin, root)) + 1
    _disk_counts[plugin] = (fetched, root, count)
    return count


def plugin_record(plugin: str, meta: dict, root: Path) -> dict:
    readme = next((root / n for n in ("README.md", "readme.md") if (root / n).exists()), None)
    body = readme.read_text(errors="replace")[:BODY_CAP] if readme else ""
    desc = meta.get("description", "")
    pj = root / ".claude-plugin" / "plugin.json"
    if not desc and pj.exists():
        try:
            desc = json.loads(pj.read_text()).get("description", "")
        except ValueError:
            pass
    return {"id": f"plugin:{plugin}", "kind": "plugin", "plugin": plugin, "name": plugin,
            "description": desc, "path": "README.md" if readme else "", "body": body,
            "hash": hashlib.sha256((desc + body).encode()).hexdigest()}


def _redact(cfg: dict) -> dict:
    """Keep the shape of a server declaration, never a literal secret. `${VAR}`
    references stay, because they say which variable the plugin expects."""
    out = {k: cfg[k] for k in ("type", "command", "args", "url") if k in cfg}
    for k in ("env", "headers"):
        if isinstance(cfg.get(k), dict):
            out[k] = {n: (v if isinstance(v, str) and re.fullmatch(r".*\$\{[A-Z0-9_]+\}.*", v)
                          else "<set>") for n, v in cfg[k].items()}
    return out


def mcp_declarations(root: Path) -> list[tuple[str, str, dict]]:
    """Servers a plugin bundles: `.mcp.json`, or `mcpServers` in plugin.json."""
    found: dict[str, dict] = {}
    for src in (root / ".mcp.json", root / ".claude-plugin" / "plugin.json"):
        if not src.exists():
            continue
        try:
            doc = json.loads(src.read_text())
        except ValueError:
            continue
        servers = doc.get("mcpServers", doc if src.name == ".mcp.json" else {})
        if isinstance(servers, dict):
            found |= {n: c for n, c in servers.items() if isinstance(c, dict)}
    return [(n, c.get("type") or ("http" if "url" in c else "stdio"), _redact(c))
            for n, c in found.items()]


_TOOL_REF = re.compile(r"mcp__[A-Za-z0-9_-]+")


def mcp_references(text: str, gateway: set[str]) -> set[tuple[str, str, str]]:
    """(route, server, tool) for each `mcp__...` tool name in the text. The route is
    the server the client connects to: CLIENT_NAME for anything behind the gateway.
    A gateway server name can contain `__`, so the longest registered name wins."""
    refs = set()
    for m in _TOOL_REF.findall(text):
        parts = m.split("__")[1:]
        if len(parts) < 2 or not all(parts):   # a trailing `__` is a placeholder
            continue
        if parts[0] == CLIENT_NAME:
            if len(parts) < 3:
                continue
            rest = parts[1:]
            server = next((s for s in ("__".join(rest[:i]) for i in range(len(rest) - 1, 0, -1))
                           if s in gateway), rest[0])
            refs.add((CLIENT_NAME, server, "__".join(rest[server.count("__") + 1:])))
        else:
            refs.add((parts[0], parts[0], "__".join(parts[1:])))
    return refs


# --- embeddings ----------------------------------------------------------------

def embed_text(r: dict) -> str:
    return f"{r['kind']} {r['name']} (plugin {r['plugin']}): {r['description']}\n\n{r['body'][:2000]}"


def embed(texts: list[str], timeout: float = 120) -> list[list[float]]:
    if not EMBED_URL:
        raise RuntimeError("EMBED_URL is not set")
    token = os.environ.get("CANOPY_API_TOKEN") or API_TOKEN_FILE.read_text().strip()
    r = httpx.post(f"{EMBED_URL}/embeddings", json={"model": EMBED_MODEL, "input": texts},
                   headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
    r.raise_for_status()
    data = sorted(r.json()["data"], key=lambda d: d["index"])
    if len(data) != len(texts):
        raise RuntimeError(f"got {len(data)} vectors for {len(texts)} inputs")
    return [d["embedding"] for d in data]


def _pending(db: store.Db) -> list[store.Row]:
    """One row per distinct text: records sharing a text share a vector. Only the part of the body that
    `embed_text` uses is read, so a first embed of a large catalogue does not hold every body in memory."""
    return db.execute(
        "SELECT DISTINCT ON (embed_key) id, kind, plugin, name, description, left(body, 2000) AS body, embed_key "
        "FROM records "
        "WHERE embedding IS NULL OR embed_model IS DISTINCT FROM %s ORDER BY embed_key, id",
        (EMBED_MODEL,)).fetchall()


def pending_estimate(db: store.Db) -> dict:
    """What an embedding run would process, so its cost can be judged first."""
    rows = _pending(db)
    return {"records": len(rows), "model": EMBED_MODEL, "configured": bool(EMBED_URL),
            "est_tokens": sum(len(embed_text(dict(r))) for r in rows) // 4}


def embed_pending(db: store.Db, batch: int = 32) -> tuple[int, str | None]:
    """Embed records whose content has no vector yet. Keyed by content hash, so
    unchanged records are never embedded twice."""
    rows = _pending(db)
    done = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        texts = [embed_text(dict(r)) for r in chunk]
        try:
            try:
                vecs = embed(texts)
            except Exception:  # noqa: BLE001 — one retry: a provider blip must not end a long run
                time.sleep(5)
                vecs = embed(texts)
        except Exception as e:  # noqa: BLE001 — reported; the records stay pending
            return done, f"embedding stopped after {done}/{len(rows)}: {e}"
        with write_lock, db:
            for r, v in zip(chunk, vecs):
                a = np.asarray(v, dtype=np.float32)
                a /= np.linalg.norm(a) or 1.0
                db.execute("UPDATE records SET embedding = %s, embed_model = %s WHERE embed_key = %s",
                           (a, EMBED_MODEL, r["embed_key"]))
        done += len(chunk)
    return done, None


# --- marketplaces --------------------------------------------------------------

def load_marketplaces() -> list[dict]:
    """`marketplaces.json`: {"marketplaces": [{"repo": "owner/name", "name": "optional"}]}."""
    doc = json.loads((CONFIG / "marketplaces.json").read_text())
    out = []
    for m in doc.get("marketplaces", []):
        repo = m["repo"]
        out.append({"repo": repo, "ref": clean_ref(m.get("ref")),
                    "name": safe_name(m.get("name") or repo.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git"))})
    return out


def parse_source(src: object, market_repo: str) -> dict | None:
    """{repo, ref, path} for the plugin source shapes marketplaces use: a relative
    "./path" inside the marketplace repo, `github`, `git-subdir` and `url`."""
    if isinstance(src, str):
        if src.startswith("./"):
            return {"repo": market_repo, "ref": None, "path": clean_subpath(src)}
        return None
    if not isinstance(src, dict):
        return None
    kind = src.get("source")
    if kind == "github" and src.get("repo"):
        repo = src["repo"]
    elif kind in ("git-subdir", "url") and src.get("url"):
        m = _GH_URL.match(src["url"])
        repo = m.group(1) if m else src["url"]
    else:
        return None
    git_url(repo)   # raises on anything but owner/repo or https
    return {"repo": repo, "ref": clean_ref(src.get("ref") or src.get("sha")),
            "path": clean_subpath(src.get("path"))}


def read_manifests(fetch: bool | set[str] = True) -> tuple[dict[str, dict], list[str], set[str]]:
    """(plugins, errors, marketplaces read). fetch=False reads the local clones as the
    last sync left them, which is what health compares against. A set fetches only the
    marketplaces it names, and any whose clone is missing."""
    plugins: dict[str, dict] = {}
    errors: list[str] = []
    healthy: set[str] = set()
    try:
        markets = load_marketplaces()
    except (OSError, ValueError, KeyError) as e:
        return {}, [f"marketplaces.json: {e}"], set()
    for mk in markets:
        name, d = mk["name"], market_dir() / mk["name"]
        try:
            if fetch is True or (isinstance(fetch, set) and (name in fetch or not (d / ".git").exists())):
                fetch_clone(mk["repo"], d, mk["ref"])
            manifest = json.loads((d / ".claude-plugin" / "marketplace.json").read_text())
        except Exception as e:  # noqa: BLE001
            errors.append(f"marketplace {name}: {e}")
            continue
        healthy.add(name)
        for p in manifest.get("plugins", []):
            try:
                pname = safe_name(p.get("name"))
                loc = parse_source(p.get("source"), mk["repo"])
            except ValueError as e:
                errors.append(f"{name}: skipped entry: {e}")
                continue
            if not loc:
                errors.append(f"{name}/{pname}: unsupported source {p.get('source')!r}")
                continue
            if pname in plugins:
                errors.append(f"{name}/{pname}: name also listed in "
                              f"{plugins[pname]['marketplace']}; indexing the {name} entry")
            plugins[pname] = {**loc, "marketplace": name, "description": p.get("description", "")}
    return plugins, errors, healthy


# --- sync ----------------------------------------------------------------------

def _store(db: store.Db, plugin: str, recs: list[dict]) -> int:
    """Replace every record of one plugin, keeping a vector whose text has not changed. A record
    whose hash and path are unchanged is not written at all. Returns the rows written or deleted.
    The caller holds the transaction."""
    gone = db.execute("DELETE FROM records WHERE plugin = %s AND NOT (id = ANY(%s)) RETURNING id",
                      (plugin, [r["id"] for r in recs])).fetchall()
    have = {r["id"]: (r["hash"], r["path"]) for r in
            db.execute("SELECT id, hash, path FROM records WHERE plugin = %s", (plugin,))}
    written = len(gone)
    for r in recs:
        if have.get(r["id"]) == (r["hash"], r["path"]):
            continue
        written += 1
        key = hashlib.sha256(embed_text(r).encode()).hexdigest()
        db.execute(
            "INSERT INTO records (id, kind, plugin, name, description, path, body, hash, embed_key) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET "
            "kind = excluded.kind, plugin = excluded.plugin, name = excluded.name, "
            "description = excluded.description, path = excluded.path, body = excluded.body, "
            "hash = excluded.hash, embed_key = excluded.embed_key, "
            "embedding = CASE WHEN records.embed_key = excluded.embed_key THEN records.embedding END, "
            "embed_model = CASE WHEN records.embed_key = excluded.embed_key THEN records.embed_model END",
            [v.replace("\x00", "") if isinstance(v, str) else v   # Postgres text cannot hold NUL
             for v in (r["id"], r["kind"], r["plugin"], r["name"], r["description"], r["path"],
                       r["body"], r["hash"], key)])
    return written


def _drop_plugin(db: store.Db, plugin: str) -> None:
    _store(db, plugin, [])
    for t in ("mcp_decls", "mcp_refs"):
        db.execute(f"DELETE FROM {t} WHERE plugin = %s", (plugin,))
    db.execute("DELETE FROM plugins WHERE name=%s", (plugin,))
    shutil.rmtree(clones() / plugin, ignore_errors=True)


def _index_plugin(db: store.Db, name: str, meta: dict, head: str, gateway: set[str]) -> int:
    fetch_clone(meta["repo"], clones() / name, meta.get("ref"))
    root = plugin_root(name, meta)
    if not root.is_dir():
        raise RuntimeError(f"path {meta.get('path')!r} not found in {meta['repo']}")
    recs = [plugin_record(name, meta, root), *extract(name, root)]
    decls = mcp_declarations(root)
    with write_lock, db:
        _store(db, name, recs)
        db.execute("DELETE FROM mcp_decls WHERE plugin=%s", (name,))
        db.execute("DELETE FROM mcp_refs WHERE plugin=%s", (name,))
        for server, transport, cfg in decls:
            db.execute("INSERT INTO mcp_decls VALUES (%s,%s,%s,%s)", (name, server, transport, json.dumps(cfg)))
        for r in recs:
            for route, server, tool in mcp_references(r["body"], gateway):
                db.execute("INSERT INTO mcp_refs VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                           (r["id"], name, route, server, tool))
        db.execute("INSERT INTO plugins VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (name) DO UPDATE SET "
                   "marketplace = excluded.marketplace, repo = excluded.repo, description = excluded.description, "
                   "head = excluded.head, status = excluded.status, error = excluded.error, "
                   "synced_at = excluded.synced_at",
                   (name, meta["marketplace"], meta["repo"], meta["description"], head, "ok", None, time.time()))
    return len(recs)


def _mark_failed(db: store.Db, name: str, meta: dict, err: str, head: str | None = None) -> None:
    """`head` is the commit that failed to index, so a quick pass does not retry it every minute.
    None (the commit could not be looked up) keeps whatever head was there."""
    with write_lock, db:
        db.execute(
            "INSERT INTO plugins (name, marketplace, repo, description, head, status, error, synced_at) "
            "VALUES (%s,%s,%s,%s,%s,'error',%s,%s) ON CONFLICT(name) DO UPDATE SET "
            "status='error', error=excluded.error, synced_at=excluded.synced_at, "
            "head=COALESCE(excluded.head, plugins.head)",
            (name, meta["marketplace"], meta["repo"], meta["description"], head, err, time.time()))


def refresh_gateway(db: store.Db) -> set[str]:
    servers = gw.api_get("servers")
    with write_lock, db:
        db.execute("DELETE FROM gateway_servers")
        for s in servers:
            db.execute("INSERT INTO gateway_servers VALUES (%s,%s,%s)",
                       (s["name"], s.get("description", ""), bool(s.get("enabled", True))))
        db.execute("INSERT INTO meta VALUES ('gateway_fetched_at', %s) "
                   "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (str(time.time()),))
    return {s["name"] for s in servers}


def group_url(group: str) -> str:
    return f"{gw.GATEWAY_URL}/v0/groups/{group}/mcp" if group else f"{gw.GATEWAY_URL}/mcp"


def refresh_tools(db: store.Db) -> int:
    """Snapshot the tools a client actually sees, and which of them are in CORE_GROUP.
    A failure keeps the last snapshot; the caller reports it."""
    tools = gw.McpClient(group_url(TOOLS_GROUP), timeout=60).tools()
    try:
        core = {t["name"] for t in gw.McpClient(group_url(CORE_GROUP), timeout=60).tools()}
    except Exception:  # noqa: BLE001 — no core group yet: everything goes via toolbox
        core = set()
    with write_lock, db:
        db.execute("DELETE FROM gateway_tools")
        for t in tools:
            if t["name"].startswith("toolbox__") or t["name"] in gw.ADMIN_TOOLS:
                continue
            db.execute("INSERT INTO gateway_tools VALUES (%s,%s,%s,%s)",
                       (t["name"], t.get("description") or "", json.dumps(t.get("inputSchema") or {}),
                        t["name"] in core))
    return len(tools)


def tool_record(name: str, description: str, schema: dict, core: bool) -> dict:
    """One `tool:<server>__<tool>` record. The body says how to call it, so a search
    hit is enough to act on; `read` returns the full schema."""
    desc = " ".join(description.split())
    if len(desc) > 600:
        desc = desc[:599] + "…"
    req = set(schema.get("required") or [])
    params = ", ".join(f"{k}{'*' if k in req else ''}" for k in schema.get("properties") or {}) or "(none)"
    how = (f"listed natively: mcp__{CLIENT_NAME}__{name}" if core else
           f'not listed natively: toolbox__call_tool(name="{name}", arguments={{...}})')
    body = f"call: {how}\nparams (* required): {params}\n"
    return {"id": f"tool:{name}", "kind": "tool", "plugin": "", "name": name, "description": desc,
            "path": "", "body": body, "hash": hashlib.sha256((desc + body).encode()).hexdigest()}


def rebuild_mcp_records(db: store.Db) -> int:
    """One `mcp:<server>` record per server that is declared, referenced or registered."""
    servers: dict[str, dict] = {}

    def entry(name: str) -> dict:
        return servers.setdefault(name, {"descs": [], "decl": [], "refs": set(), "routes": set()})

    for g in db.execute("SELECT name, description FROM gateway_servers"):
        entry(g["name"])["descs"].append(g["description"])
        entry(g["name"])["routes"].add(CLIENT_NAME)
    for d in db.execute("SELECT plugin, server FROM mcp_decls"):
        entry(d["server"])["decl"].append(d["plugin"])
    for r in db.execute("SELECT DISTINCT plugin, route, server FROM mcp_refs"):
        entry(r["server"])["refs"].add(r["plugin"])
        entry(r["server"])["routes"].add(r["route"])
    recs = []
    for name, e in sorted(servers.items()):
        desc = next((d for d in e["descs"] if d), "") or (
            f"MCP server declared by {', '.join(e['decl'])}" if e["decl"]
            else f"MCP server referenced by {', '.join(sorted(e['refs']))}")
        body = (f"routes: {', '.join(sorted(e['routes']))}\ndeclared by: {', '.join(e['decl']) or '-'}\n"
                f"referenced by: {', '.join(sorted(e['refs'])) or '-'}\n")
        recs.append({"id": f"mcp:{name}", "kind": "mcp", "plugin": "", "name": name, "description": desc,
                     "path": "", "body": body, "hash": hashlib.sha256((desc + body).encode()).hexdigest()})
    for t in db.execute("SELECT * FROM gateway_tools"):
        recs.append(tool_record(t["name"], t["description"], json.loads(t["schema"]), bool(t["core"])))
    with write_lock, db:
        written = _store(db, "", recs)
    return len(recs), written


def refresh_gateway_records(db: store.Db) -> dict:
    """The gateway's servers and tools, and the mcp and tool records built from them. Not part of the
    plugin pass: these change when the gateway does, not when a repo is pushed. Only records whose
    text changed are written. The summary is kept in meta as `gateway_refresh` for health."""
    started, errors = time.time(), []
    try:
        refresh_gateway(db)
    except Exception as e:  # noqa: BLE001 — keep the last list, but say so
        errors.append(f"gateway server list: {e}")
    try:
        refresh_tools(db)
    except Exception as e:  # noqa: BLE001 — keep the last snapshot, but say so
        errors.append(f"gateway tool list: {e}")
    count, written = rebuild_mcp_records(db)
    summary = {"ok": not errors, "mode": "gateway", "mcp_and_tool_records": count, "records_written": written,
               "changed": written > 0, "errors": errors, "at": time.time(),
               "seconds": round(time.time() - started, 1)}
    with write_lock, db:
        db.execute("INSERT INTO meta VALUES ('gateway_refresh', %s) "
                   "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (json.dumps(summary),))
    if written or errors:
        log(f"gateway refresh: {json.dumps(summary)[:1000]}")
    return summary


def stale(row: dict, name: str, meta: dict, head: str, full: bool = False) -> bool:
    """A restored database remembers heads whose clones were never backed up, so the disk counts too.
    A plugin that failed at this same commit waits for the commit to move, or for a full pass."""
    if row.get("status") == "error" and row.get("head") == head and not full:
        return False
    return (row.get("head") != head or row.get("status") != "ok" or not plugin_root(name, meta).exists()
            or (row.get("description") or "") != (meta.get("description") or ""))   # edited in the marketplace only


# --- change detection ------------------------------------------------------------

Key = tuple[str, "str | None"]   # (repo, ref)

_TARGET = "target { oid ... on Tag { target { oid } } }"


def _graphql_heads(keys: list[Key], token: str = GIT_TOKEN, post=httpx.post) -> dict[Key, str]:
    """The commit for each (owner/repo, ref) GitHub resolves, GRAPHQL_BATCH repositories per call. A
    repository or ref it cannot resolve is left out, for the caller to ask git. An annotated tag
    resolves to its commit, as `remote_head` does, so the two never disagree about a head."""
    out: dict[Key, str] = {}
    for i in range(0, len(keys), GRAPHQL_BATCH):
        chunk = keys[i:i + GRAPHQL_BATCH]
        fields = []
        for j, (repo, ref) in enumerate(chunk):
            owner, name = repo.split("/")
            sel = f"ref(qualifiedName: {json.dumps(ref)}) {{ {_TARGET} }}" if ref else f"defaultBranchRef {{ {_TARGET} }}"
            fields.append(f"r{j}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {sel} }}")
        r = post("https://api.github.com/graphql", json={"query": "{ " + " ".join(fields) + " }"}, timeout=30,
                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
        r.raise_for_status()
        data = r.json().get("data") or {}
        for j, (repo, ref) in enumerate(chunk):
            target = ((data.get(f"r{j}") or {}).get("ref" if ref else "defaultBranchRef") or {}).get("target") or {}
            oid = (target.get("target") or {}).get("oid") or target.get("oid")
            if oid:
                out[(repo, ref)] = oid
    return out


def remote_heads(keys: set[Key]) -> tuple[dict[Key, str], dict[Key, str]]:
    """(heads, errors) for many repos at once. A pinned sha needs no lookup. GitHub repos go in batched
    GraphQL calls when there is a token, a few calls for hundreds of repos. The rest, and anything
    GraphQL did not resolve, go through `ls-remote`."""
    heads: dict[Key, str] = {}
    errors: dict[Key, str] = {}
    github = []
    for repo, ref in keys:
        if ref and _SHA.fullmatch(ref):
            heads[(repo, ref)] = ref
        elif GIT_TOKEN and _REPO.fullmatch(repo):
            github.append((repo, ref))
    if github:
        try:
            heads |= _graphql_heads(sorted(github, key=str))
        except Exception as e:  # noqa: BLE001 - git can still answer, one repo at a time
            log(f"GraphQL head check failed, asking git instead: {e!r}")

    def one(key: Key) -> None:
        try:
            heads[key] = remote_head(*key)
        except Exception as e:  # noqa: BLE001
            errors[key] = str(e)

    with ThreadPoolExecutor(16) as ex:
        list(ex.map(one, [k for k in keys if k not in heads]))
    return heads, errors


def _key(meta: dict) -> Key:
    return (meta["repo"], meta.get("ref"))


# --- the pass --------------------------------------------------------------------

def sync(db: store.Db, only: str | None = None, force: bool = False, full: bool = False) -> dict:
    """One pass over the marketplaces and their plugins. `ok` in the summary is False if anything failed.

    1. Look up the latest commit of every marketplace and plugin repo, batched (`remote_heads`).
    2. Fetch a marketplace again only if its commit moved, and read every manifest.
    3. Reindex a plugin that is new, whose commit moved, whose marketplace entry changed or whose clone
       is gone. Drop one its marketplace no longer lists.
    4. Rebuild the mcp records only if a plugin changed.

    A quick pass (the default) with nothing to do writes nothing, not even a `runs` row. Without a
    GitHub token it checks only the marketplaces' own repos, and the plugins in them; other plugin
    repos wait for the full pass. `full` fetches every marketplace, checks every repo and retries
    plugins in error. `force` reindexes whatever it checks. `only` checks one plugin.
    The gateway's servers and tools are not part of this: see `refresh_gateway_records`."""
    started = time.time()
    mode = "plugin" if only else "full" if full else "quick"
    errors: list[str] = []
    try:
        markets, config_ok = load_marketplaces(), True
    except (OSError, ValueError, KeyError):
        markets, config_ok = [], False   # read_manifests reports it; an unreadable config must not prune anything
    local, _, _ = read_manifests(fetch=False)
    known = {r["name"]: dict(r) for r in db.execute("SELECT * FROM plugins")}
    market_heads = {r["name"]: r["head"] for r in db.execute("SELECT name, head FROM marketplaces")}

    def checked(meta: dict) -> bool:
        return bool(GIT_TOKEN) or full or bool(only) or any(meta["repo"] == m["repo"] for m in markets)

    keys = {_key(m) for m in markets}
    keys |= {_key(p) for n, p in local.items() if (n == only if only else checked(p))}
    heads, head_errors = remote_heads(keys)

    moved = {m["name"] for m in markets
             if full or only or (_key(m) in heads and heads[_key(m)] != market_heads.get(m["name"]))}
    plugins, merr, healthy = read_manifests(fetch=moved)
    errors += merr
    fetched = [m for m in markets if m["name"] in moved and m["name"] in healthy]
    unlisted_state = config_ok and bool(market_heads.keys() - {m["name"] for m in markets})
    if fetched or unlisted_state:
        with write_lock, db:
            for m in fetched:
                db.execute("INSERT INTO marketplaces VALUES (%s,%s,%s,%s,%s) ON CONFLICT (name) DO UPDATE SET "
                           "repo = excluded.repo, ref = excluded.ref, head = excluded.head, "
                           "fetched_at = excluded.fetched_at",
                           (m["name"], m["repo"], m["ref"], heads.get(_key(m)), time.time()))
            if unlisted_state:
                db.execute("DELETE FROM marketplaces WHERE NOT (name = ANY(%s))", ([m["name"] for m in markets],))
    for m in markets:
        if _key(m) in head_errors:
            errors.append(f"marketplace {m['name']}: {head_errors[_key(m)]}")

    targets = {only: plugins[only]} if only and only in plugins else ({} if only else plugins)
    if only and not targets:
        errors.append(f"{only}: not in any marketplace manifest")
    # A plugin is checked if its repo is, or if it is new, or its entry changed in the marketplace.
    targets = {n: p for n, p in targets.items()
               if checked(p) or n not in known or (known[n].get("description") or "") != (p.get("description") or "")}
    missing = {_key(p) for p in targets.values()} - heads.keys() - head_errors.keys()
    if missing:
        more, more_errors = remote_heads(missing)
        heads |= more
        head_errors |= more_errors
    for n, p in targets.items():
        if _key(p) in head_errors:
            errors.append(f"{n}: {head_errors[_key(p)]}")
            if mode != "quick" or known.get(n, {}).get("status") != "error":   # no write every minute
                _mark_failed(db, n, p, head_errors[_key(p)])

    todo = [n for n, p in targets.items()
            if _key(p) in heads and (force or stale(known.get(n, {}), n, p, heads[_key(p)], full))]
    written: list[int] = []
    gateway = {r["name"] for r in db.execute("SELECT name FROM gateway_servers")}

    def work(name: str) -> None:
        head = heads[_key(targets[name])]
        try:
            written.append(_index_plugin(db, name, targets[name], head, gateway))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
            _mark_failed(db, name, targets[name], str(e), head)

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(work, todo))

    removed = []
    if not only:
        configured = {m["name"] for m in markets} if config_ok else None
        for name, row in known.items():
            # A failed fetch must not look like every plugin was deleted, so a plugin
            # goes only if its marketplace was read, or was removed from the config.
            dropped = name not in plugins and row["marketplace"] in healthy
            unlisted = configured is not None and row["marketplace"] not in configured
            if dropped or unlisted:
                with write_lock, db:
                    _drop_plugin(db, name)
                removed.append(name)
        if configured is not None:
            for d in market_dir().glob("*"):
                if d.name not in configured:
                    shutil.rmtree(d, ignore_errors=True)

    changed = bool(todo or removed)
    _, mcp_written = rebuild_mcp_records(db) if changed or full else (0, 0)
    summary = {"ok": not errors, "mode": mode, "manifest_plugins": len(plugins), "checked": len(targets),
               "marketplaces_fetched": sorted(moved & healthy), "reindexed_plugins": len(todo),
               "records_written": sum(written), "removed_plugins": removed,
               "mcp_records_written": mcp_written, "changed": changed or mcp_written > 0,
               "embed_pending": pending_estimate(db) if changed else None, "errors": errors,
               "seconds": round(time.time() - started, 1)}
    if mode != "quick" or changed or fetched:
        with write_lock, db:
            db.execute("INSERT INTO runs (started, finished, ok, summary) VALUES (%s,%s,%s,%s)",
                       (started, time.time(), not errors, json.dumps(summary)))
        log(f"sync: {json.dumps(summary)[:1000]}")
    elif errors:
        log(f"quick pass, nothing changed, errors left for the full pass: {errors[:5]}")
    return summary
