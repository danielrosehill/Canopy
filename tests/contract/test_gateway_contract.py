"""One test per gateway behaviour Canopy relies on. See docs/architecture.md, "Gateway behaviour
the seed depends on". A failure here on a new MCPJungle version names what changed.

Measured against 0.4.7 on 2026-11-05. Where a test pins something odd, it says why it matters.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import pytest

from canopy import gateway as gw
from canopy.control import Gateway
from conftest import UPSTREAM_URL, enterprise_only, group_tools, server

MCP_INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "contract", "version": "1"}}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def two_tools(name: str) -> list[str]:
    return [f"{name}__{name}_a", f"{name}__{name}_b"]


def mcp_status(token: str) -> int:
    return httpx.post(f"{gw.GATEWAY_URL}/mcp", json=MCP_INIT, timeout=10,
                      headers={**MCP_HEADERS, **bearer(token)}).status_code


def rest_status(token: str, path: str = "servers") -> int:
    return httpx.get(f"{gw.GATEWAY_URL}/api/v0/{path}", headers=bearer(token), timeout=10).status_code


# --- start-up ---------------------------------------------------------------------------


def test_metadata_reports_the_version_under_test(g):
    tag = os.environ.get("MCPJUNGLE_TAG") or json.loads(Path("compat.json").read_text())["mcpjungle"]["pinned"]
    meta = httpx.get(f"{gw.GATEWAY_URL}/metadata", timeout=5)
    assert meta.json()["version"].lstrip("v") == tag.split("-")[0]


@enterprise_only
def test_init_gives_the_admin_token_only_once(admin_token):
    assert admin_token
    r = httpx.post(f"{gw.GATEWAY_URL}/init", json={"mode": "enterprise"}, timeout=10)
    assert r.status_code == 400 and "admin_access_token" not in r.text


# --- identities -------------------------------------------------------------------------


@enterprise_only
def test_a_client_token_works_on_mcp_only(g, tmp_path, uid):
    g.ensure_identity("client", f"c{uid}", tmp_path / "t")
    token = (tmp_path / "t").read_text().strip()
    assert mcp_status(token) == 200
    assert rest_status(token) == 401


@enterprise_only
def test_a_user_token_reads_rest_only(g, tmp_path, uid):
    g.ensure_identity("user", f"u{uid}", tmp_path / "t")
    token = (tmp_path / "t").read_text().strip()
    assert rest_status(token) == 200 and rest_status(token, "tools") == 200
    assert mcp_status(token) == 401
    r = httpx.delete(f"{gw.GATEWAY_URL}/api/v0/servers/nosuch", headers=bearer(token), timeout=10)
    assert r.status_code == 403


@enterprise_only
def test_no_token_is_refused():
    assert httpx.get(f"{gw.GATEWAY_URL}/api/v0/servers", timeout=10).status_code == 401
    assert httpx.post(f"{gw.GATEWAY_URL}/mcp", json=MCP_INIT, headers=MCP_HEADERS, timeout=10).status_code == 401


@enterprise_only
def test_a_lost_token_is_replaced_and_the_old_one_dies(g, tmp_path, uid):
    """The gateway cannot show a token again, so `ensure_identity` deletes and recreates."""
    path = tmp_path / "t"
    assert g.ensure_identity("client", f"c{uid}", path) is True
    old = path.read_text().strip()
    assert g.ensure_identity("client", f"c{uid}", path) is False
    path.unlink()
    assert g.ensure_identity("client", f"c{uid}", path) is True
    assert mcp_status(path.read_text().strip()) == 200
    assert mcp_status(old) == 401


# --- servers ----------------------------------------------------------------------------


def test_rest_registers_and_removes_a_server(g, uid):
    name = f"s{uid}"
    g.register(server(name))
    assert name in g.server_names()
    g.deregister(name)
    assert name not in g.server_names()


def test_a_second_registration_fails_so_register_deletes_first(g, uid):
    name = f"s{uid}"
    g.register(server(name))
    assert g.http.post("/api/v0/servers", json=server(name)).status_code >= 400
    g.register(server(name))
    assert name in g.server_names()


def test_deleting_an_unknown_server_is_a_404_not_a_crash(g):
    assert g.http.delete("/api/v0/servers/nosuch").status_code == 404


def test_a_registration_can_time_out_although_it_worked(g, uid):
    """The server is listed at once and its tools follow, even though the client has left."""
    name = f"slow{uid}"
    with pytest.raises(httpx.TimeoutException):
        g.http.post("/api/v0/servers", json=server(name), timeout=1)
    assert name in g.server_names()
    assert not [t for t in g.tools() if t["name"].startswith(f"{name}__")]
    deadline = time.time() + 15
    while not [t for t in g.tools() if t["name"].startswith(f"{name}__")]:
        assert time.time() < deadline, "the gateway dropped the tools when the client left"
        time.sleep(0.5)


def test_register_waits_for_the_tools_after_a_timeout(admin_token, uid):
    name = f"slow{uid}"
    impatient = Gateway(admin_token)
    impatient.http.timeout = httpx.Timeout(1)
    impatient.register(server(name))
    assert sorted(t["name"] for t in impatient.tools() if t["name"].startswith(f"{name}__")) == two_tools(name)


def test_an_unreachable_upstream_is_refused_and_not_listed(g, uid):
    name = f"s{uid}"
    r = g.http.post("/api/v0/servers", json={**server(name), "url": "http://upstream:9/mcp"})
    assert r.status_code >= 400
    assert name not in g.server_names()


def test_tool_lists_freeze_at_registration(g, uid):
    """Why the `tool-refresh` job exists: only registering again picks up a new tool."""
    name = f"s{uid}"
    g.register(server(name))
    httpx.post(f"{UPSTREAM_URL}/extra/{name}", json={"tool": "late"}, timeout=5).raise_for_status()
    wanted = f"{name}__late"
    assert wanted not in {t["name"] for t in g.tools()}
    g.register(server(name))
    assert wanted in {t["name"] for t in g.tools()}


# --- tools ------------------------------------------------------------------------------


def test_rest_lists_tools_for_an_admin(g, uid):
    name = f"s{uid}"
    g.register(server(name))
    mine = {t["name"]: t for t in g.tools() if t["name"].startswith(f"{name}__")}
    assert sorted(mine) == two_tools(name)
    assert mine[f"{name}__{name}_a"]["description"]


def test_a_tool_call_reaches_the_upstream_under_its_own_name(g, uid):
    name = f"s{uid}"
    g.register(server(name))
    reply = gw.McpClient(f"{gw.GATEWAY_URL}/mcp", timeout=20).call(f"{name}__{name}_a")
    assert reply["content"][0]["text"] == f"{name}:{name}_a"


def test_server_records_carry_the_fields_the_index_reads(g, uid):
    name = f"s{uid}"
    g.register(server(name))
    record = next(s for s in g.servers() if s["name"] == name)
    assert record["enabled"] is True and record["description"] == "Contract fixture"


# --- groups -----------------------------------------------------------------------------


def test_a_group_naming_an_unregistered_server_is_refused(g, uid):
    r = g.http.post("/api/v0/tool-groups", json={"name": f"g{uid}", "included_servers": ["nosuch"]})
    assert r.status_code == 404
    assert g.http.get(f"/api/v0/tool-groups/g{uid}").status_code == 404


def test_a_group_with_no_tools_is_refused(g, uid):
    g.register(server(f"empty{uid}"))
    r = g.http.post("/api/v0/tool-groups", json={"name": f"g{uid}", "included_servers": [f"empty{uid}"]})
    assert r.status_code == 400


def test_update_keeps_lists_it_was_not_given(g, uid):
    """Why groups are deleted and created: after a PUT that omits a list, the stored definition keeps
    the old one while the live group drops it, and they disagree until the gateway restarts."""
    a, b, group = f"a{uid}", f"b{uid}", f"g{uid}"
    g.register(server(a))
    g.register(server(b))
    g.http.post("/api/v0/tool-groups", json={"name": group, "included_tools": [f"{a}__{a}_a"]}).raise_for_status()
    g.http.put(f"/api/v0/tool-groups/{group}", json={"name": group, "included_servers": [b]}).raise_for_status()
    stored = g.http.get(f"/api/v0/tool-groups/{group}").json()
    assert stored["included_tools"] == [f"{a}__{a}_a"] and stored["included_servers"] == [b]
    assert group_tools(group) == two_tools(b)


def test_rebuild_replaces_a_group_exactly(g, uid):
    a, b, group = f"a{uid}", f"b{uid}", f"g{uid}"
    g.register(server(a))
    g.register(server(b))
    g.rebuild_group({"name": group, "included_tools": [f"{a}__{a}_a"]}, {a, b})
    g.rebuild_group({"name": group, "included_servers": [b]}, {a, b})
    stored = g.http.get(f"/api/v0/tool-groups/{group}").json()
    assert not stored.get("included_tools") and stored["included_servers"] == [b]
    assert group_tools(group) == two_tools(b)


def test_rebuild_checks_membership_before_it_deletes(g, uid):
    a, group = f"a{uid}", f"g{uid}"
    g.register(server(a))
    g.rebuild_group({"name": group, "included_servers": [a]}, {a})
    with pytest.raises(RuntimeError, match="unregistered"):
        g.rebuild_group({"name": group, "included_servers": ["nosuch"]}, {a})
    assert group_tools(group) == two_tools(a)


def test_a_failed_create_restores_the_old_group(g, uid):
    a, empty, group = f"a{uid}", f"empty{uid}", f"g{uid}"
    g.register(server(a))
    g.register(server(empty))
    g.rebuild_group({"name": group, "included_servers": [a]}, {a, empty})
    with pytest.raises(RuntimeError, match="restored"):
        g.rebuild_group({"name": group, "included_servers": [empty]}, {a, empty})
    assert group_tools(group) == two_tools(a)


def test_a_new_server_does_not_join_a_group_that_does_not_name_it(g, uid):
    a, b, group = f"a{uid}", f"b{uid}", f"g{uid}"
    g.register(server(a))
    g.rebuild_group({"name": group, "included_servers": [a]}, {a})
    g.register(server(b))
    assert group_tools(group) == two_tools(a)


def test_a_group_resolves_its_servers_by_name_across_a_reregistration(g, uid):
    """A group follows the name: empty while the server is gone, whole when it returns. So the rebuild
    after a registration is a precaution on this version, not a need."""
    a, group = f"a{uid}", f"g{uid}"
    g.register(server(a))
    g.rebuild_group({"name": group, "included_servers": [a]}, {a})
    g.deregister(a)
    assert group_tools(group) == []
    g.register(server(a))
    assert group_tools(group) == two_tools(a)


def test_included_and_excluded_tools_are_honoured(g, uid):
    a, b = f"a{uid}", f"b{uid}"
    g.register(server(a))
    g.register(server(b))
    g.rebuild_group({"name": f"i{uid}", "included_tools": [f"{a}__{a}_b"]}, {a, b})
    g.rebuild_group({"name": f"x{uid}", "included_servers": [b], "excluded_tools": [f"{b}__{b}_a"]}, {a, b})
    assert group_tools(f"i{uid}") == [f"{a}__{a}_b"]
    assert group_tools(f"x{uid}") == [f"{b}__{b}_b"]


def test_delete_group_is_verified_by_a_read(g, uid):
    a, group = f"a{uid}", f"g{uid}"
    g.register(server(a))
    g.rebuild_group({"name": group, "included_servers": [a]}, {a})
    g.delete_group(group)
    assert group not in {x["name"] for x in g.groups()}
