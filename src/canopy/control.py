"""Control logic behind the API: bring the gateway to the state the config describes.

Only this code holds the gateway's admin token. It works over the gateway's REST API, since
the gateway's CLI is not in this image. Writes are believed only once a read agrees.

Group rules, each learned from a failure:

- A group update keeps any list it was not given, and the stored and live groups then
  disagree until a restart, so groups are deleted and created.
- A create that names an unregistered server fails after the delete, so membership is
  checked first, and the old definition is restored if the create still fails.
- A new server does not join an existing group by itself, so groups are rebuilt
  whenever a server was registered.

The gateway has no update for a server or a group, so a change is a delete and a create. If the
create fails, whatever was there goes back: a bad edit must not cost something that worked. A
server's old definition comes from the gateway, since only it holds the expanded credentials.
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
REGISTER_SETTLE = float(os.environ.get("REGISTER_SETTLE_SEC", "30"))
ALERT_WEBHOOK = os.environ.get("CANOPY_ALERT_WEBHOOK", "")

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

    def tools(self, server: str | None = None) -> list[dict]:
        r = self.http.get("/api/v0/tools", params={"server": server} if server else None)
        r.raise_for_status()
        return r.json()

    def set_server_enabled(self, name: str, on: bool) -> None:
        self.http.post(f"/api/v0/servers/{name}/{'enable' if on else 'disable'}").raise_for_status()
        record = next((s for s in self.servers() if s["name"] == name), {})
        if record.get("enabled") is not on:
            raise RuntimeError(f"the gateway still has {name} enabled={record.get('enabled')}")

    def set_tool_enabled(self, name: str, on: bool) -> None:
        self.http.post(f"/api/v0/tools/{'enable' if on else 'disable'}", params={"entity": name}).raise_for_status()
        record = next((t for t in self.tools(name.split("__")[0]) if t["name"] == name), {})
        if record.get("enabled") is not on:
            raise RuntimeError(f"the gateway still has {name} enabled={record.get('enabled')}")

    def saved_server(self, name: str) -> dict | None:
        """What `name` was registered with, in the form a registration takes."""
        r = self.http.get("/api/v0/server_configs")
        r.raise_for_status()
        return next((c for c in r.json() if c.get("name") == name), None)

    def _put_back(self, previous: dict | None) -> str:
        """Words for the error, so that it says what state the failure left."""
        if previous is None:
            return ""
        name = previous["name"]
        try:
            self.http.delete(f"/api/v0/servers/{name}")
            self.http.post("/api/v0/servers", json=previous)
            if name in self.server_names():
                return "; the previous definition was restored"
        except httpx.HTTPError as e:
            return f"; restoring the previous definition failed too: {e}"
        return "; restoring the previous definition failed too"

    def register(self, server: dict) -> None:
        """A registration can time out even though it worked, so the server list decides. The
        gateway lists a server before it has fetched the tools, so after a timeout the tools
        have to arrive too, or a group built next would miss them."""
        name = server["name"]
        previous = self.saved_server(name)
        self.http.delete(f"/api/v0/servers/{name}")
        err, timed_out = "", False
        try:
            r = self.http.post("/api/v0/servers", json=server)
            if not r.is_success:
                err = r.text[:300]
        except httpx.TimeoutException:
            err, timed_out = "timed out", True
        if name not in self.server_names():
            raise RuntimeError((err or "the gateway accepted it, but does not list it") + self._put_back(previous))
        if timed_out and not self._tools_arrive(name):
            raise RuntimeError(f"timed out, and no tools arrived within {REGISTER_SETTLE:.0f}s"
                               + self._put_back(previous))

    def _tools_arrive(self, name: str) -> bool:
        deadline = time.time() + REGISTER_SETTLE
        while not any(t["name"].startswith(f"{name}__") for t in self.tools()):
            if time.time() > deadline:
                return False
            time.sleep(0.5)
        return True

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
        if not live.is_success and live.status_code != 404:   # no copy to go back to, so change nothing
            raise RuntimeError(f"cannot read the current group: {live.status_code}")
        self.http.delete(f"/api/v0/tool-groups/{name}")
        created, timed_out = False, False
        try:
            r = self.http.post("/api/v0/tool-groups", json=group)
            created, why = r.is_success, r.text[:300]
        except httpx.TimeoutException as e:   # as with a server, it may have worked anyway
            timed_out, why = True, str(e) or "timed out"
        except httpx.HTTPError as e:
            why = str(e)
        try:
            listed = self.http.get(f"/api/v0/tool-groups/{name}").is_success
        except httpx.HTTPError as e:
            raise RuntimeError(f"cannot tell whether group {name} exists: {e}") from None
        if listed and (created or timed_out):
            return
        if created:
            raise RuntimeError("the gateway accepted it, but does not list it")
        if not live.is_success:
            raise RuntimeError(f"create failed: {why}")
        old = {k: v for k, v in live.json().items() if not k.endswith("_endpoint")}
        try:
            restored = self.http.post("/api/v0/tool-groups", json=old).is_success
        except httpx.HTTPError:
            restored = False
        raise RuntimeError(f"create failed, previous definition {'restored' if restored else 'NOT restored'}: {why}")

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
                 mode: str = MODE, env: dict[str, str] | None = None, alert_url: str = ALERT_WEBHOOK,
                 post: Callable = httpx.post):
        self.make_gateway, self.sync = gateway, sync
        self.config, self.admin_dir, self.token_dir, self.mode = config, admin_dir, token_dir, mode
        self.env = os.environ if env is None else env
        self.alert_url, self.post = alert_url, post
        self.alerted: list[str] = []   # the parts last reported as failing
        self.lock = threading.RLock()
        self.last_apply: dict | None = None
        self.extra_checks: dict[str, Callable[[], dict]] = {}   # parts /v1/status reports, set by `api`
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
        write_json(self.state_path, state)

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

    @staticmethod
    def _prunable(doc: dict, state: dict) -> tuple[list[str], dict[str, str]]:
        """Servers dropped from the config: those to remove, and those a group still holds. Only names
        in the state file are candidates, so a server registered by hand is never touched. A missing or
        empty config reads the same as one that was lost, so it removes nothing: use remove_server to
        take the last server out."""
        if not doc["servers"]:
            return [], {}
        keep = BUILTIN_NAMES | {s.get("name") for s in doc["servers"]}
        grouped = {n: x["name"] for x in merged_groups(doc["groups"]) for n in group_servers(x)}
        dropped = [n for n in state if not n.startswith("#") and n not in keep]
        return [n for n in dropped if n not in grouped], {n: grouped[n] for n in dropped if n in grouped}

    def _prune(self, g: Gateway, doc: dict, state: dict, say, failures: list[str]) -> None:
        if not doc["servers"]:
            say("no servers in the config, so nothing was pruned")
            return
        remove, held = self._prunable(doc, state)
        failures += [f"{n}: dropped from the config but still in group {group}" for n, group in held.items()]
        for name in remove:
            try:
                g.deregister(name)
            except (RuntimeError, httpx.HTTPError) as e:
                failures.append(f"{name}: {e}")
                continue
            state.pop(name)
            self.write_state(state)
            say(f"removed {name}")

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
                    except (RuntimeError, httpx.HTTPError) as e:
                        ok = False
                        failures.append(f"group {group['name']}: {e}")
                if ok:
                    state["#groups"] = d
                    self.write_state(state)

            self._prune(g, doc, state, log.append, failures)

            err = self.sync()
            if err:
                failures.append(f"catalogue sync: {err}")
            else:
                log.append("asked the catalogue to sync")
            self.last_apply = {"at": time.time(), "ok": not failures, "failures": failures}
            return {"ok": not failures, "log": log, "failures": failures}

    # --- check before applying ------------------------------------------------------

    def validate(self) -> dict:
        """What is wrong with the config, without touching anything. Groups are checked against the
        config and, if it answers, the gateway, since a group may name a server registered by hand."""
        try:
            doc = json.loads(self.servers_path.read_text()) if self.servers_path.exists() else {}
        except ValueError as e:
            return {"ok": False, "problems": [f"servers.json is not JSON: {e}"]}
        if not isinstance(doc, dict) or not all(isinstance(doc.get(k, []), list) for k in ("servers", "groups")):
            return {"ok": False, "problems": ["servers.json must be an object with `servers` and `groups` lists"]}
        problems, seen = [], set()
        for s in doc.get("servers", []):
            name = s.get("name") if isinstance(s, dict) else None
            try:
                check_name(name)
            except ControlError as e:
                problems.append(f"server: {e}")
                continue
            if name in seen:
                problems.append(f"{name}: named twice")
            if name in BUILTIN_NAMES:
                problems.append(f"{name}: is built in")
            seen.add(name)
            try:
                expand(s, self.env)
            except KeyError as e:
                problems.append(f"{name}: environment variable {e.args[0]} is not set")
        known = seen | BUILTIN_NAMES
        try:
            known |= self.gateway().server_names()
        except (httpx.HTTPError, ControlError):
            pass
        groups = set()
        for x in doc.get("groups", []):
            name = x.get("name") if isinstance(x, dict) else None
            try:
                check_name(name)
            except ControlError as e:
                problems.append(f"group: {e}")
                continue
            if name in groups:
                problems.append(f"group {name}: named twice")
            groups.add(name)
            if missing := group_servers(x) - known:
                problems.append(f"group {name}: names unregistered servers: {sorted(missing)}")
        return {"ok": not problems, "problems": problems}

    def diff(self) -> dict:
        """What `apply` would do now, by name only. Values stay out: they may hold credentials."""
        checked = self.validate()
        if not checked["ok"]:
            return {**checked, "register": [], "remove": [], "blocked": {}, "rebuild_groups": []}
        doc, state, live = self.read_config(), self.read_state(), self.gateway().server_names()
        register = []
        for raw in [*BUILTIN_SERVERS, *doc["servers"]]:
            name = raw["name"]
            if name not in live:
                register.append({"name": name, "why": "not on the gateway"})
            elif state.get(name) != digest(expand(raw, self.env)):
                register.append({"name": name, "why": "changed" if name in state else "not registered by apply"})
        remove, held = self._prunable(doc, state)
        wanted = merged_groups(doc["groups"])
        rebuild = [x["name"] for x in wanted] if register or state.get("#groups") != digest(wanted) else []
        return {"ok": True, "problems": [], "register": register, "remove": remove, "blocked": held,
                "rebuild_groups": rebuild,
                "unmanaged": sorted(live - BUILTIN_NAMES - {s["name"] for s in doc["servers"]} - set(remove) - set(held))}

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
            rebuilt = self._rebuild_for(g, doc, name)
        return {"registered": name, "rebuilt_groups": rebuilt, "sync_error": self.sync()}

    @staticmethod
    def _rebuild_for(g: Gateway, doc: dict, name: str) -> list[str]:
        """On 0.4.7 a group follows the name, so this is a precaution. See the contract suite."""
        registered, rebuilt = g.server_names(), []
        for group in merged_groups(doc["groups"]):
            if name in group_servers(group):
                try:
                    g.rebuild_group(group, registered)
                except RuntimeError as e:
                    raise ControlError(502, f"{name} is registered, but group {group['name']}: {e}") from None
                rebuilt.append(group["name"])
        return rebuilt

    def _listed(self, g: Gateway, name: str) -> dict:
        check_name(name)
        record = next((s for s in g.servers() if s["name"] == name), None)
        if record is None:
            raise ControlError(404, f"no server {name}")
        return record

    def _bad_gateway(self, fn, *args) -> None:
        try:
            fn(*args)
        except RuntimeError as e:
            raise ControlError(502, str(e)) from None

    def set_server_enabled(self, name: str, on: bool) -> dict:
        """Takes an upstream out without forgetting it. This is the gateway's state, not the config's."""
        if name in BUILTIN_NAMES and not on:
            raise ControlError(400, f"{name} is built in")
        with self.lock:
            g = self.gateway()
            self._listed(g, name)
            self._bad_gateway(g.set_server_enabled, name, on)
        return {"server": name, "enabled": on, "sync_error": self.sync()}

    def set_tool_enabled(self, name: str, on: bool) -> dict:
        server = check_name(name).split("__")[0]
        if server in BUILTIN_NAMES and not on:
            raise ControlError(400, f"{server} is built in")
        with self.lock:
            g = self.gateway()
            if not any(t["name"] == name for t in g.tools(server)):
                raise ControlError(404, f"no tool {name}")
            self._bad_gateway(g.set_tool_enabled, name, on)
        return {"tool": name, "enabled": on, "sync_error": self.sync()}

    def _refresh(self, g: Gateway, name: str) -> dict:
        """Register a server again as it stands, the only way the gateway re-reads its tools. That
        clears what was disabled, so it is disabled again afterwards."""
        record = self._listed(g, name)
        before = {t["name"]: t.get("enabled") is not False for t in g.tools(name)}
        self._bad_gateway(g.register, g.saved_server(name))
        if record.get("enabled") is False:
            self._bad_gateway(g.set_server_enabled, name, False)
        after = {t["name"] for t in g.tools(name)}
        for tool in sorted(after):
            if before.get(tool) is False and record.get("enabled") is not False:
                self._bad_gateway(g.set_tool_enabled, tool, False)
        return {"refreshed": name, "tools": len(after), "added": sorted(after - set(before)),
                "removed": sorted(set(before) - after), "rebuilt_groups": self._rebuild_for(g, self.read_config(), name)}

    def refresh_server(self, name: str) -> dict:
        with self.lock:
            reply = self._refresh(self.gateway(), name)
        return {**reply, "sync_error": self.sync()}

    def refresh_job(self) -> str:
        """The `tool-refresh` job. Disabled servers are left alone, and the built-ins change only with
        an update. One failure does not stop the rest, and each failure keeps its old definition."""
        lines, failures = [], []
        with self.lock:
            g = self.gateway()
            for s in sorted(g.servers(), key=lambda s: s["name"]):
                if s["name"] in BUILTIN_NAMES or s.get("enabled") is False:
                    continue
                try:
                    r = self._refresh(g, s["name"])
                    if r["added"] or r["removed"]:
                        lines.append(f"{s['name']}: +{len(r['added'])} -{len(r['removed'])}")
                except (ControlError, httpx.HTTPError) as e:
                    failures.append(f"{s['name']}: {e}")
        err = self.sync()
        if err:
            failures.append(f"catalogue sync: {err}")
        if failures:
            raise RuntimeError("; ".join(failures))
        return "; ".join(lines) or "no tool lists changed"

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

    def _tool_text(self, name: str, arguments: dict | None = None) -> str:
        reply = self._mcp.call(name, arguments)
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

    def health_job(self) -> str:
        """The `health` job: a failed check fails the run, so the record shows when it broke."""
        report = self.status()
        self.alert(report)
        if not report["ok"]:
            raise RuntimeError(json.dumps(report)[:2000])
        return json.dumps(report)

    def alert(self, report: dict) -> None:
        """Tell the webhook when the set of failing parts changes, and only then, so an outage is one
        message and its end another. `text` suits Slack and Mattermost, `content` Discord. If the
        message cannot be sent it is tried again on the next run."""
        failing = {k: v for k, v in report["components"].items() if not v["ok"]}
        names = sorted(failing)
        if names == self.alerted or not self.alert_url:
            return
        text = (f"Canopy: failing: {', '.join(names)}" if names
                else f"Canopy: recovered: {', '.join(self.alerted)}")
        try:
            self.post(self.alert_url, timeout=10, json={
                "text": text, "content": text, "ok": not names,
                "failing": {k: json.dumps(v)[:500] for k, v in failing.items()}}).raise_for_status()
        except httpx.HTTPError as e:
            print(f"alert not sent: {e!r}", flush=True)
            return
        self.alerted = names

    def status(self) -> dict:
        checks = {"gateway": self._check_gateway, "upstreams": self._check_upstreams,
                  "groups": self._check_groups, "catalogue": self._check_catalogue,
                  "toolbox": self._check_toolbox, **self.extra_checks}

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
