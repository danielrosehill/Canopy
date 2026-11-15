"""The hub against two real upstreams: the contract fixture over streamable HTTP, and an SDK server over stdio.
Each test is a behaviour the MCPJungle contract suite had to work around, stated as what Canopy wants."""
from __future__ import annotations

import asyncio
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent / "contract"))
import upstream as http_fixture  # noqa: E402

from canopy.hub import Hub, HubError  # noqa: E402

STDIO = str(Path(__file__).parent / "fixtures" / "stdio_upstream.py")


@pytest.fixture(scope="module")
def http_url():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), http_fixture.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def servers(http_url: str, **extra) -> list[dict]:
    return [
        {"name": "web", "transport": "streamable_http", "url": f"{http_url}/mcp/web", "description": "HTTP fixture"},
        {"name": "local", "transport": "stdio", "command": sys.executable, "args": [STDIO]},
        *extra.get("more", []),
    ]


def run(http_url, body, **extra):
    async def go():
        hub = Hub(servers(http_url, **extra), env=extra.get("env", {}))
        await hub.start()
        try:
            return await body(hub)
        finally:
            await hub.stop()
    return asyncio.run(go())


def text(result) -> str:
    return result.content[0].text


def test_tools_from_both_transports_are_named_by_server(http_url):
    async def body(hub):
        return sorted(t["name"] for t in hub.tools())
    assert run(http_url, body) == ["local__crash", "local__echo", "local__grow", "local__whoami", "web__web_a",
                                    "web__web_b"]


def test_a_call_reaches_its_own_upstream(http_url):
    async def body(hub):
        return text(await hub.call("web__web_a")), text(await hub.call("local__echo", {"text": "hi"}))
    assert run(http_url, body) == ("web:web_a", "stdio:hi")


def test_an_unreachable_server_is_reported_and_the_rest_work(http_url):
    dead = {"name": "dead", "transport": "streamable_http", "url": "http://127.0.0.1:9/mcp"}

    async def body(hub):
        status = {s["name"]: s for s in hub.servers()}
        assert not status["dead"]["up"] and status["dead"]["error"]
        assert status["web"]["up"] and status["local"]["up"]
        with pytest.raises(HubError, match="dead is not connected"):
            await hub.call("dead__x")
        return text(await hub.call("web__web_b"))
    assert run(http_url, body, more=[dead]) == "web:web_b"


def test_a_missing_credential_fails_that_server_only(http_url):
    keyed = {"name": "keyed", "transport": "streamable_http", "url": f"{http_url}/mcp/keyed",
             "bearer_token": "${NOT_SET}"}

    async def body(hub):
        return {s["name"]: s["error"] for s in hub.servers()}
    errors = run(http_url, body, more=[keyed])
    assert "NOT_SET" in errors["keyed"] and errors["web"] is None


def test_refresh_picks_up_a_new_tool_without_reregistering(http_url):
    async def body(hub):
        httpx.post(f"{http_url}/extra/web", json={"tool": "web_new"})
        before = {t["name"] for t in hub.tools()}
        await hub.refresh("web")
        return before, {t["name"] for t in hub.tools()}
    before, after = run(http_url, body)
    assert "web__web_new" not in before and "web__web_new" in after


def test_a_list_changed_notification_refreshes_on_its_own(http_url):
    async def body(hub):
        await hub.call("local__grow", {"name": "sprout"})
        for _ in range(50):
            if "local__sprout" in {t["name"] for t in hub.tools()}:
                return text(await hub.call("local__sprout"))
            await asyncio.sleep(0.1)
        return None
    assert run(http_url, body) == "stdio:sprout"


def test_a_server_that_dies_is_reconnected_on_the_next_call(http_url):
    async def body(hub):
        with pytest.raises(Exception):
            await hub.call("local__crash")
        for _ in range(50):
            if not hub.upstreams["local"].up:
                break
            await asyncio.sleep(0.1)
        return text(await hub.call("local__echo", {"text": "back"}))
    assert run(http_url, body) == "stdio:back"


def test_allow_works_tool_by_tool(http_url):
    def allow(name):   # all of `web`, and only `echo` of `local`
        return name.startswith("web__") or name == "local__echo"

    async def body(hub):
        names = {t["name"] for t in hub.tools(allow)}   # `web` may hold tools an earlier test added
        assert {"web__web_a", "web__web_b"} <= names and {n for n in names if n.startswith("local__")} == {"local__echo"}
        assert {s["name"]: s["tools"] for s in hub.servers(allow)}["local"] == 1
        with pytest.raises(HubError, match="no tool 'local__crash'"):
            await hub.call("local__crash", allow=allow)
        return text(await hub.call("local__echo", {"text": "ok"}, allow=allow))
    assert run(http_url, body) == "stdio:ok"


def accounts_hub(env=None):
    letters = {"name": "letters", "transport": "stdio", "command": sys.executable, "args": [STDIO],
               "accounts": {"personal": {"env": {"ACCOUNT": "personal"}},
                            "family": {"env": {"ACCOUNT": "family"}},
                            "business": {"env": {"ACCOUNT": "${BIZ}"}}}}
    mail = {"name": "mail", "transport": "stdio", "command": sys.executable, "args": [STDIO],
            "account_argument": "email",
            "accounts": {"home": {"value": "home@example.org"}, "work": {"value": "${WORK_MAIL}"}}}
    return Hub([letters, mail], env=env if env is not None else {"BIZ": "business", "WORK_MAIL": "w@example.org"})


def with_accounts(body, env=None):
    async def go():
        hub = accounts_hub(env)
        await hub.start()
        try:
            return await body(hub)
        finally:
            await hub.stop()
    return asyncio.run(go())


def test_a_server_per_account_starts_one_session_and_the_rest_on_first_use():
    async def body(hub):
        before = sorted(k for k, u in hub.upstreams.items() if u.up)
        family = text(await hub.call("letters__whoami", account="family"))
        after = sorted(k for k, u in hub.upstreams.items() if u.up)
        return before, family, after
    before, family, after = with_accounts(body)
    assert before == ["letters:personal", "mail"]
    assert family == "family|"
    assert after == ["letters:family", "letters:personal", "mail"]


def test_each_account_runs_with_its_own_key():
    async def body(hub):
        return [text(await hub.call("letters__whoami", account=a)) for a in ("personal", "family", "business")]
    assert with_accounts(body) == ["personal|", "family|", "business|"]


def test_an_argument_account_is_set_by_the_hub_and_cannot_be_overridden():
    async def body(hub):
        work = text(await hub.call("mail__whoami", account="work"))
        with pytest.raises(HubError, match="choose the account with `account`"):
            await hub.call("mail__whoami", {"email": "home@example.org"}, account="work")
        return work
    assert with_accounts(body) == "-|w@example.org"


def test_the_caller_only_reaches_its_own_accounts():
    def only_family(server, every):
        return [a for a in every if a in ("family", "home")]

    async def body(hub):
        alone = text(await hub.call("letters__whoami", accounts=only_family))   # one account: no need to name it
        with pytest.raises(HubError, match="no account 'personal'"):
            await hub.call("letters__whoami", account="personal", accounts=only_family)
        with pytest.raises(HubError, match="several accounts"):
            await hub.call("letters__whoami")
        chosen = text(await hub.call("letters__whoami", default=lambda s: "business"))
        rows = {r["name"]: r["accounts"] for r in hub.servers(accounts=only_family)}
        none = hub.servers(accounts=lambda s, every: [])
        return alone, chosen, rows, none
    alone, chosen, rows, none = with_accounts(body)
    assert alone == "family|" and chosen == "business|"
    assert rows == {"letters": ["family"], "mail": ["home"]}
    assert none == []   # no account on a server means no server


def test_an_account_with_a_missing_key_fails_alone():
    async def body(hub):
        with pytest.raises(HubError, match="BIZ"):
            await hub.call("letters__whoami", account="business")
        return text(await hub.call("letters__whoami", account="family"))
    assert with_accounts(body, env={"WORK_MAIL": "w@example.org"}) == "family|"


def test_bad_names_are_refused():
    with pytest.raises(HubError, match="may not contain"):
        Hub([{"name": "a__b", "transport": "stdio", "command": "true"}])
    with pytest.raises(HubError, match="twice"):
        Hub([{"name": "a", "command": "true"}, {"name": "a", "command": "true"}])
