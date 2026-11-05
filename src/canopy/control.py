"""Control logic behind the API: bring the gateway to the state the config describes.

Only this code holds the gateway's admin token. It works over the gateway's REST API, since
the gateway's CLI is not in this image. Writes are believed only once a read agrees.

Group rules, each learned from a failure:

- A group update only adds servers it has not seen, so groups are deleted and created.
- A create that names an unregistered server fails after the delete, so membership is
  checked first, and the old definition is restored if the create still fails.
- A new server does not join an existing group by itself, so groups are rebuilt
  whenever a server was registered.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import httpx

from . import gateway as gw

ADMIN_DIR = Path(os.environ.get("CANOPY_ADMIN_DIR", "/admin"))
CONFIG = Path(os.environ.get("CANOPY_CONFIG", "/config"))
MODE = os.environ.get("SERVER_MODE", "enterprise")
CORE_GROUP = os.environ.get("CORE_GROUP", "core")
REGISTER_TIMEOUT = float(os.environ.get("REGISTER_TIMEOUT_SEC", "30"))

BUILTIN_SERVERS = [
    {"name": "catalogue", "transport": "streamable_http", "url": "http://catalogue:3000/mcp",
     "description": "Search and read skills, plugins, MCP servers and tools"},
    {"name": "toolbox", "transport": "streamable_http", "url": "http://toolbox:3000/mcp",
     "description": "Call any gateway tool that is not listed natively"},
]
BUILTIN_NAMES = {s["name"] for s in BUILTIN_SERVERS}
AGENT_TOKEN, ADMIN_TOKEN, STATE = "agent", "admin", "registered.json"
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class ControlError(Exception):
    """A request that cannot be done, with the HTTP status that says why."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def check_name(name: object) -> str:
    """Names go into gateway URLs, so they may not contain a path."""
    if not isinstance(name, str) or not _NAME.fullmatch(name) or ".." in name:
        raise ControlError(400, f"invalid name {name!r}")
    return name


def match_owner(path: Path) -> None:
    """Files take the owner of their folder, so that `canopy init`'s user can read them."""
    try:
        st = path.parent.stat()
        os.chown(path, st.st_uid, st.st_gid)
    except OSError:
        pass


def write_secret(path: Path, value: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=mode)
    path.chmod(mode)
    path.write_text(value + "\n")
    match_owner(path)


def write_json(path: Path, doc: dict) -> None:
    """Atomic, so a crash cannot leave half a config."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, path)
    match_owner(path)


def expand(value: object, env: dict[str, str]) -> object:
    """Replace ${VAR} in every string. A variable that is not set raises: registering
    an upstream with an empty credential fails later and less clearly."""
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            if m[1] not in env:
                raise KeyError(m[1])
            return env[m[1]]
        return _VAR.sub(sub, value)
    if isinstance(value, list):
        return [expand(v, env) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, env) for k, v in value.items()}
    return value


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def merged_groups(groups: list[dict]) -> list[dict]:
    """User groups, with the core group always carrying catalogue and toolbox."""
    builtin = [s["name"] for s in BUILTIN_SERVERS]
    out = [dict(g) for g in groups]
    core = next((g for g in out if g["name"] == CORE_GROUP), None)
    if core is None:
        core = {"name": CORE_GROUP, "description": "Search plus the long-tail tool caller"}
        out.insert(0, core)
    core["included_servers"] = list(dict.fromkeys([*builtin, *core.get("included_servers", [])]))
    return out


def group_servers(group: dict) -> set[str]:
    named = {t.split("__")[0] for t in group.get("included_tools", [])}
    return set(group.get("included_servers", [])) | named


class Gateway:
    """The gateway's REST API, with reads that check each write."""

    def __init__(self, token: str, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(base_url=gw.GATEWAY_URL, timeout=REGISTER_TIMEOUT, transport=transport,
                                 headers={"Authorization": f"Bearer {token}"} if token else {})

    def servers(self) -> list[dict]:
        r = self.http.get("/api/v0/servers")
        r.raise_for_status()
        return r.json()

    def server_names(self) -> set[str]:
        return {s["name"] for s in self.servers()}

    def groups(self) -> list[dict]:
        r = self.http.get("/api/v0/tool-groups")
        r.raise_for_status()
        return r.json()

    def tools(self) -> list[dict]:
        r = self.http.get("/api/v0/tools")
        r.raise_for_status()
        return r.json()

    def register(self, server: dict) -> None:
        """A registration can time out even though it worked, so the server list decides."""
        name = server["name"]
        self.http.delete(f"/api/v0/servers/{name}")
        err = ""
        try:
            r = self.http.post("/api/v0/servers", json=server)
            if not r.is_success:
                err = r.text[:300]
        except httpx.TimeoutException:
            err = "timed out"
        if name not in self.server_names():
            raise RuntimeError(err or "the gateway accepted it, but does not list it")

    def deregister(self, name: str) -> None:
        self.http.delete(f"/api/v0/servers/{name}")
        if name in self.server_names():
            raise RuntimeError(f"the gateway still lists {name}")

    def ensure_identity(self, kind: str, name: str, path: Path) -> bool:
        """Create an identity with a token we choose and store. If the token file was
        lost, the identity is replaced, since the gateway cannot show it again."""
        if path.exists() and path.read_text().strip():
            return False
        token = secrets.token_urlsafe(32)
        endpoint = {"client": "clients", "user": "users"}[kind]
        self.http.delete(f"/api/v0/{endpoint}/{name}")
        body = ({"name": name, "allow_list": ["*"], "access_token": token} if kind == "client"
                else {"username": name, "access_token": token})
        self.http.post(f"/api/v0/{endpoint}", json=body).raise_for_status()
        write_secret(path, token)
        return True

    def rebuild_group(self, group: dict, registered: set[str]) -> None:
        name = group["name"]
        missing = group_servers(group) - registered
        if missing:
            raise RuntimeError(f"names unregistered servers: {sorted(missing)}")
        live = self.http.get(f"/api/v0/tool-groups/{name}")
        self.http.delete(f"/api/v0/tool-groups/{name}")
        r = self.http.post("/api/v0/tool-groups", json=group)
        if r.is_success:
            if not self.http.get(f"/api/v0/tool-groups/{name}").is_success:
                raise RuntimeError("the gateway accepted it, but does not list it")
            return
        if live.is_success:
            old = {k: v for k, v in live.json().items() if not k.endswith("_endpoint")}
            self.http.post("/api/v0/tool-groups", json=old)
            raise RuntimeError(f"create failed, previous definition restored: {r.text[:300]}")
        raise RuntimeError(f"create failed: {r.text[:300]}")

    def delete_group(self, name: str) -> None:
        self.http.delete(f"/api/v0/tool-groups/{name}")
        if self.http.get(f"/api/v0/tool-groups/{name}").is_success:
            raise RuntimeError(f"the gateway still lists group {name}")


def wait_for_gateway(seconds: int = 120) -> None:
    deadline = time.time() + seconds
    while True:
        try:
            if httpx.get(f"{gw.GATEWAY_URL}/health", timeout=5).is_success:
                return
        except httpx.HTTPError:
            pass
        if time.time() > deadline:
            raise ControlError(503, f"gateway did not answer at {gw.GATEWAY_URL} within {seconds}s")
        time.sleep(2)


def ask_catalogue_to_sync(attempts: int = 5) -> str | None:
    """The error, if the catalogue could not be reached."""
    for attempt in range(attempts):
        try:
            gw.McpClient(f"{gw.GATEWAY_URL}/mcp", timeout=30, name="canopy-api").call("catalogue__sync")
            return None
        except Exception as e:  # noqa: BLE001
            if attempt == attempts - 1:
                return str(e)
            time.sleep(3)
    return None


class Control:
    """Every change to the gateway goes through here, one at a time."""

    def __init__(self, gateway: Callable[[str], Gateway] = Gateway, sync: Callable[[], str | None] = ask_catalogue_to_sync,
                 config: Path = CONFIG, admin_dir: Path = ADMIN_DIR, token_dir: Path = gw.TOKEN_DIR,
                 mode: str = MODE, env: dict[str, str] | None = None):
        self.make_gateway, self.sync = gateway, sync
        self.config, self.admin_dir, self.token_dir, self.mode = config, admin_dir, token_dir, mode
        self.env = os.environ if env is None else env
        self.lock = threading.RLock()
        self.last_apply: dict | None = None
        self._mcp = gw.McpClient(f"{gw.GATEWAY_URL}/mcp", timeout=10, name="canopy-status")

    # --- files ---------------------------------------------------------------------

    @property
    def servers_path(self) -> Path:
        return self.config / "servers.json"

    def read_config(self) -> dict:
        doc = json.loads(self.servers_path.read_text()) if self.servers_path.exists() else {}
        return {"servers": doc.get("servers", []), "groups": doc.get("groups", [])}

    @property
    def state_path(self) -> Path:
        return self.admin_dir / STATE

    def read_state(self) -> dict:
        return json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def write_state(self, state: dict) -> None:
        self.admin_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2))

    # --- identity ------------------------------------------------------------------

    def admin_token(self) -> str:
        """The admin token, initialising the server the first time."""
        if self.mode != "enterprise":
            return ""
        path = self.admin_dir / ADMIN_TOKEN
        if path.exists():
            return path.read_text().strip()
        r = httpx.post(f"{gw.GATEWAY_URL}/init", json={"mode": "enterprise"}, timeout=30)
        if not r.is_success:
            raise ControlError(500, "The gateway is already initialised but the admin token is gone. "
                                    "Restore it from a backup, or start again with an empty data folder, "
                                    "which deletes the gateway's data.")
        token = r.json()["admin_access_token"]
        write_secret(path, token)
        return token

    def gateway(self) -> Gateway:
        return self.make_gateway(self.admin_token())

    def agent_token(self) -> str | None:
        """None in development mode, where the gateway has no identities."""
        if self.mode != "enterprise":
            return None
        path = self.admin_dir / AGENT_TOKEN
        if not path.exists():
            raise ControlError(409, "no agent token yet: the seed has not finished")
        return path.read_text().strip()

    # --- apply ---------------------------------------------------------------------

    def _register_all(self, g: Gateway, servers: list[dict], state: dict, say) -> tuple[bool, list[str]]:
        """Register what changed. Returns (anything registered, failures)."""
        changed, failures = False, []
        live = g.server_names()
        for raw in [*BUILTIN_SERVERS, *servers]:
            name = raw.get("name", "?")
            builtin = raw in BUILTIN_SERVERS
            try:
                server = expand(raw, self.env)
            except KeyError as e:
                failures.append(f"{name}: environment variable {e.args[0]} is not set")
                continue
            d = digest(server)
            if state.get(name) == d and name in live:
                continue
            for attempt in range(20 if builtin else 1):   # built-ins may still be starting
                try:
                    g.register(server)
                    state[name] = d
                    changed = True
                    say(f"registered {name}")
                    break
                except (RuntimeError, httpx.HTTPError) as e:
                    if attempt == (19 if builtin else 0):
                        failures.append(f"{name}: {e}")
                    else:
                        time.sleep(3)
        return changed, failures

    def apply(self) -> dict:
        """Bring the gateway to what the config describes. Safe to repeat."""
        log: list[str] = []
        with self.lock:
            wait_for_gateway()
            g = self.gateway()
            if self.mode == "enterprise":
                for kind, name, path in (("client", "canopy-services", self.token_dir / gw.SERVICES_TOKEN),
                                         ("user", "canopy-reader", self.token_dir / gw.READER_TOKEN),
                                         ("client", "canopy-agent", self.admin_dir / AGENT_TOKEN)):
                    if g.ensure_identity(kind, name, path):
                        log.append(f"created {kind} {name}")
            doc = self.read_config()
            state = self.read_state()
            changed, failures = self._register_all(g, doc["servers"], state, log.append)
            self.write_state(state)

            wanted = merged_groups(doc["groups"])
            d = digest(wanted)
            if changed or state.get("#groups") != d:
                registered = g.server_names()
                ok = True
                for group in wanted:
                    try:
                        g.rebuild_group(group, registered)
                        log.append(f"built group {group['name']}")
                    except RuntimeError as e:
                        ok = False
                        failures.append(f"group {group['name']}: {e}")
                if ok:
                    state["#groups"] = d
                    self.write_state(state)

            err = self.sync()
            if err:
                failures.append(f"catalogue sync: {err}")
            else:
                log.append("asked the catalogue to sync")
            self.last_apply = {"at": time.time(), "ok": not failures, "failures": failures}
            return {"ok": not failures, "log": log, "failures": failures}

    # --- single changes ------------------------------------------------------------

    def add_server(self, raw: dict, replace: bool = False) -> dict:
        name = check_name(raw.get("name"))
        if name in BUILTIN_NAMES:
            raise ControlError(400, f"{name} is built in")
        try:
            server = expand(raw, self.env)
        except KeyError as e:
            raise ControlError(400, f"environment variable {e.args[0]} is not set") from None
        with self.lock:
            g = self.gateway()
            if name in g.server_names() and not replace:
                raise ControlError(409, f"{name} is already registered; set replace to overwrite it")
            try:
                g.register(server)
            except RuntimeError as e:
                raise ControlError(502, f"{name}: {e}") from None
            doc = self.read_config()
            doc["servers"] = [s for s in doc["servers"] if s.get("name") != name] + [raw]
            write_json(self.servers_path, doc)
            state = self.read_state()
            state[name] = digest(server)
            self.write_state(state)
            # Re-registering drops a server from its groups' tool sets.
            registered = g.server_names()
            rebuilt = []
            for group in merged_groups(doc["groups"]):
                if name in group_servers(group):
                    g.rebuild_group(group, registered)
                    rebuilt.append(group["name"])
        return {"registered": name, "rebuilt_groups": rebuilt, "sync_error": self.sync()}

    def remove_server(self, name: str) -> dict:
        check_name(name)
        if name in BUILTIN_NAMES:
            raise ControlError(400, f"{name} is built in")
        with self.lock:
            doc = self.read_config()
            using = [x["name"] for x in merged_groups(doc["groups"]) if name in group_servers(x)]
            if using:
                raise ControlError(409, f"{name} is in groups {using}: change those first")
            g = self.gateway()
            known = name in g.server_names() or any(s.get("name") == name for s in doc["servers"])
            if not known:
                raise ControlError(404, f"no server {name}")
            try:
                g.deregister(name)
            except RuntimeError as e:
                raise ControlError(502, str(e)) from None
            doc["servers"] = [s for s in doc["servers"] if s.get("name") != name]
            write_json(self.servers_path, doc)
            state = self.read_state()
            state.pop(name, None)
            self.write_state(state)
        return {"removed": name, "sync_error": self.sync()}

    def set_group(self, group: dict) -> dict:
        name = check_name(group.get("name"))
        with self.lock:
            doc = self.read_config()
            doc["groups"] = [x for x in doc["groups"] if x.get("name") != name] + [group]
            wanted = next(x for x in merged_groups(doc["groups"]) if x["name"] == name)
            g = self.gateway()
            try:
                g.rebuild_group(wanted, g.server_names())
            except RuntimeError as e:
                raise ControlError(400, f"group {name}: {e}") from None
            write_json(self.servers_path, doc)
        return {"group": name}

    def remove_group(self, name: str) -> dict:
        check_name(name)
        if name == CORE_GROUP:
            raise ControlError(400, f"{name} is the core group")
        with self.lock:
            g = self.gateway()
            try:
                g.delete_group(name)
            except RuntimeError as e:
                raise ControlError(502, str(e)) from None
            doc = self.read_config()
            doc["groups"] = [x for x in doc["groups"] if x.get("name") != name]
            write_json(self.servers_path, doc)
        return {"removed": name}

    # --- status --------------------------------------------------------------------

    def _check_gateway(self) -> dict:
        health = httpx.get(f"{gw.GATEWAY_URL}/health", timeout=5)
        meta = httpx.get(f"{gw.GATEWAY_URL}/metadata", timeout=5)
        return {"ok": health.is_success, "mode": self.mode,
                "version": meta.json().get("version") if meta.is_success else None}

    def _check_upstreams(self) -> dict:
        """A server list that answers also proves the gateway can reach its database."""
        servers = self.gateway().servers()
        names = {s["name"] for s in servers}
        wanted = {s.get("name") for s in self.read_config()["servers"]} | BUILTIN_NAMES
        missing = sorted(wanted - names)
        disabled = sorted(s["name"] for s in servers if s.get("enabled") is False)
        return {"ok": not missing, "registered": len(names), "missing": missing, "disabled": disabled}

    def _check_groups(self) -> dict:
        names = {x["name"] for x in self.gateway().groups()}
        wanted = {x["name"] for x in merged_groups(self.read_config()["groups"])}
        missing = sorted(wanted - names)
        return {"ok": not missing, "groups": len(names), "missing": missing}

    def _tool_text(self, name: str) -> str:
        reply = self._mcp.call(name)
        text = "".join(c.get("text", "") for c in reply.get("content", []))
        if reply.get("isError"):
            raise RuntimeError(text[:300])
        return text

    def _check_catalogue(self) -> dict:
        health = json.loads(self._tool_text("catalogue__health"))
        return {"ok": bool(health.get("ok")), "counts": health.get("counts"),
                "problems": health.get("problems", []), "sync_running": health.get("sync_running")}

    def _check_toolbox(self) -> dict:
        self._tool_text("toolbox__list_servers")
        return {"ok": True}

    def status(self) -> dict:
        checks = {"gateway": self._check_gateway, "upstreams": self._check_upstreams,
                  "groups": self._check_groups, "catalogue": self._check_catalogue,
                  "toolbox": self._check_toolbox}

        def run(check: Callable[[], dict]) -> dict:
            try:
                return check()
            except Exception as e:  # noqa: BLE001 - one broken part must not hide the others
                return {"ok": False, "error": str(e)[:300]}

        with ThreadPoolExecutor(len(checks)) as pool:
            results = dict(zip(checks, pool.map(run, checks.values())))
        if self.last_apply and not self.last_apply["ok"]:
            results["apply"] = {"ok": False, "failures": self.last_apply["failures"]}
        return {"ok": all(r["ok"] for r in results.values()), "components": results}
