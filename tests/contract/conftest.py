"""Fixtures for the contract suite. It needs a running gateway, so it only runs from `run.sh`,
which sets GATEWAY_URL and friends before anything imports `canopy`."""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import httpx
import pytest

if "CONTRACT_UPSTREAM_URL" not in os.environ:
    collect_ignore_glob = ["test_*.py"]
else:
    from canopy import gateway as gw
    from canopy.control import Gateway

MODE = os.environ.get("SERVER_MODE", "enterprise")
UPSTREAM_URL = os.environ.get("CONTRACT_UPSTREAM_URL", "")

enterprise_only = pytest.mark.skipif(MODE != "enterprise", reason="development mode has no identities")


def server(name: str) -> dict:
    return {"name": name, "transport": "streamable_http", "url": f"http://upstream:3000/mcp/{name}",
            "description": "Contract fixture"}


def group_tools(group: str) -> list[str]:
    """What an agent on this group sees, from a fresh session."""
    url = f"{gw.GATEWAY_URL}/v0/groups/{group}/mcp"
    return sorted(t["name"] for t in gw.McpClient(url, timeout=20).tools())


@pytest.fixture(scope="session")
def admin_token() -> str:
    deadline = time.time() + 60
    while True:
        try:
            if httpx.get(f"{gw.GATEWAY_URL}/health", timeout=3).is_success:
                break
        except httpx.HTTPError:
            pass
        if time.time() > deadline:
            pytest.fail(f"no gateway at {gw.GATEWAY_URL}")
        time.sleep(1)
    if MODE != "enterprise":
        return ""
    r = httpx.post(f"{gw.GATEWAY_URL}/init", json={"mode": "enterprise"}, timeout=30)
    r.raise_for_status()
    return r.json()["admin_access_token"]


@pytest.fixture(scope="session")
def g(admin_token: str) -> "Gateway":
    """Canopy's own client, so a test covers our call as well as the gateway's answer."""
    return Gateway(admin_token)


@pytest.fixture(scope="session", autouse=True)
def identities(g: "Gateway") -> None:
    """The service identity `group_tools` and McpClient use, made the way `apply` makes it."""
    if MODE == "enterprise":
        g.ensure_identity("client", "contract-services", Path(gw.TOKEN_DIR) / gw.SERVICES_TOKEN)
        g.ensure_identity("user", "contract-reader", Path(gw.TOKEN_DIR) / gw.READER_TOKEN)


@pytest.fixture
def uid() -> str:
    """Tests share one gateway, so every name is unique to its test."""
    return uuid.uuid4().hex[:8]
