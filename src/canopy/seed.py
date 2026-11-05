"""The seed service: bring the gateway to the state the config describes, then exit.

    python -m canopy.seed           bootstrap, register upstreams, build groups
    python -m canopy.seed token     print the agent token

Idempotent, so it runs on every `docker compose up`. It works over the REST API only:
the gateway's CLI is not in this image, and the API is complete.

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
import sys
import time
from pathlib import Path

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
AGENT_TOKEN, ADMIN_TOKEN, STATE = "agent", "admin", "registered.json"
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def say(msg: str) -> None:
    print(msg, flush=True)


def write_secret(path: Path, value: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=mode)
    path.chmod(mode)
    path.write_text(value + "\n")


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


def load_config() -> tuple[list[dict], list[dict]]:
    path = CONFIG / "servers.json"
    doc = json.loads(path.read_text()) if path.exists() else {}
    return doc.get("servers", []), doc.get("groups", [])


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
    def __init__(self, token: str):
        self.http = httpx.Client(base_url=gw.GATEWAY_URL, timeout=REGISTER_TIMEOUT,
                                 headers={"Authorization": f"Bearer {token}"} if token else {})

    def server_names(self) -> set[str]:
        r = self.http.get("/api/v0/servers")
        r.raise_for_status()
        return {s["name"] for s in r.json()}

    def register(self, server: dict) -> None:
        """A registration can time out even though it worked, so a failure is only
        believed once the server list agrees."""
        name = server["name"]
        self.http.delete(f"/api/v0/servers/{name}")
        err = ""
        try:
            r = self.http.post("/api/v0/servers", json=server)
            if r.is_success:
                return
            err = r.text[:300]
        except httpx.TimeoutException:
            err = "timed out"
        if name not in self.server_names():
            raise RuntimeError(err)

    def ensure_identity(self, kind: str, name: str, path: Path) -> None:
        """Create an identity with a token we choose and store. If the token file was
        lost, the identity is replaced, since the gateway cannot show it again."""
        if path.exists() and path.read_text().strip():
            return
        token = secrets.token_urlsafe(32)
        endpoint = {"client": "clients", "user": "users"}[kind]
        self.http.delete(f"/api/v0/{endpoint}/{name}")
        body = ({"name": name, "allow_list": ["*"], "access_token": token} if kind == "client"
                else {"username": name, "access_token": token})
        self.http.post(f"/api/v0/{endpoint}", json=body).raise_for_status()
        write_secret(path, token)
        say(f"  created {kind} {name}")

    def rebuild_group(self, group: dict, registered: set[str]) -> None:
        name = group["name"]
        missing = group_servers(group) - registered
        if missing:
            raise RuntimeError(f"names unregistered servers: {sorted(missing)}")
        live = self.http.get(f"/api/v0/tool-groups/{name}")
        self.http.delete(f"/api/v0/tool-groups/{name}")
        r = self.http.post("/api/v0/tool-groups", json=group)
        if r.is_success:
            return
        if live.is_success:
            old = {k: v for k, v in live.json().items() if not k.endswith("_endpoint")}
            self.http.post("/api/v0/tool-groups", json=old)
            raise RuntimeError(f"create failed, previous definition restored: {r.text[:300]}")
        raise RuntimeError(f"create failed: {r.text[:300]}")


def wait_for_gateway(seconds: int = 120) -> None:
    deadline = time.time() + seconds
    while True:
        try:
            if httpx.get(f"{gw.GATEWAY_URL}/health", timeout=5).is_success:
                return
        except httpx.HTTPError:
            pass
        if time.time() > deadline:
            sys.exit(f"gateway did not answer at {gw.GATEWAY_URL} within {seconds}s")
        time.sleep(2)


def bootstrap() -> str:
    """The admin token, initialising the server the first time."""
    if MODE != "enterprise":
        return ""
    path = ADMIN_DIR / ADMIN_TOKEN
    if path.exists():
        return path.read_text().strip()
    r = httpx.post(f"{gw.GATEWAY_URL}/init", json={"mode": "enterprise"}, timeout=30)
    if not r.is_success:
        sys.exit("The gateway is already initialised but the admin token is gone from the "
                 "`admin` volume. Restore it from a backup, or start again with "
                 "`docker compose down -v`, which deletes the gateway's data.")
    token = r.json()["admin_access_token"]
    write_secret(path, token)
    say("  initialised the gateway")
    return token


def register_all(g: Gateway, servers: list[dict], state: dict, env: dict[str, str]) -> tuple[bool, list[str]]:
    """Register what changed. Returns (anything registered, failures)."""
    changed, failures = False, []
    live = g.server_names()
    for raw in [*BUILTIN_SERVERS, *servers]:
        name = raw.get("name", "?")
        builtin = raw in BUILTIN_SERVERS
        try:
            server = expand(raw, env)
        except KeyError as e:
            failures.append(f"{name}: environment variable {e.args[0]} is not set")
            continue
        digest = hashlib.sha256(json.dumps(server, sort_keys=True).encode()).hexdigest()
        if state.get(name) == digest and name in live:
            continue
        for attempt in range(20 if builtin else 1):   # built-ins may still be starting
            try:
                g.register(server)
                state[name] = digest
                changed = True
                say(f"  registered {name}")
                break
            except (RuntimeError, httpx.HTTPError) as e:
                if attempt == (19 if builtin else 0):
                    failures.append(f"{name}: {e}")
                else:
                    time.sleep(3)
    return changed, failures


def seed() -> int:
    wait_for_gateway()
    token = bootstrap()
    g = Gateway(token)
    if MODE == "enterprise":
        g.ensure_identity("client", "canopy-services", gw.TOKEN_DIR / gw.SERVICES_TOKEN)
        g.ensure_identity("user", "canopy-reader", gw.TOKEN_DIR / gw.READER_TOKEN)
        g.ensure_identity("client", "canopy-agent", ADMIN_DIR / AGENT_TOKEN)

    servers, groups = load_config()
    state_path = ADMIN_DIR / STATE
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    changed, failures = register_all(g, servers, state, dict(os.environ))
    ADMIN_DIR.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))

    wanted = merged_groups(groups)
    digest = hashlib.sha256(json.dumps(wanted, sort_keys=True).encode()).hexdigest()
    if changed or state.get("#groups") != digest:
        registered = g.server_names()
        ok = True
        for group in wanted:
            try:
                g.rebuild_group(group, registered)
                say(f"  built group {group['name']}")
            except RuntimeError as e:
                ok = False
                failures.append(f"group {group['name']}: {e}")
        if ok:
            state["#groups"] = digest
            state_path.write_text(json.dumps(state, indent=2))

    for attempt in range(5):
        try:
            gw.McpClient(f"{gw.GATEWAY_URL}/mcp", timeout=30, name="canopy-seed").call("catalogue__sync")
            say("  asked the catalogue to sync")
            break
        except Exception as e:  # noqa: BLE001
            if attempt == 4:
                failures.append(f"catalogue sync: {e}")
            time.sleep(3)

    for f in failures:
        say(f"  FAILED {f}")
    say(f"seed finished with {len(failures)} problem(s)")
    return 1 if failures else 0


def main() -> None:
    if sys.argv[1:] == ["token"]:
        print((ADMIN_DIR / AGENT_TOKEN).read_text().strip())
        return
    sys.exit(seed())


if __name__ == "__main__":
    main()
