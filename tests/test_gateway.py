import pytest

from canopy import gateway as gw


def test_parse_plain_and_sse():
    assert gw.parse_mcp_response('{"id": 1}') == {"id": 1}
    assert gw.parse_mcp_response('event: message\ndata: {"id": 2}\n\n') == {"id": 2}
    assert gw.parse_mcp_response("") is None


def test_parse_skips_a_notification_ahead_of_the_response():
    """mcp-go sends tools/list_changed on a session's next reply after a server is registered."""
    stream = ('event: message\ndata: {"jsonrpc":"2.0","method":"notifications/tools/list_changed"}\n\n'
              'event: message\ndata: {"jsonrpc":"2.0","id":7,"result":{"content":[]}}\n\n')
    assert gw.parse_mcp_response(stream, 7) == {"jsonrpc": "2.0", "id": 7, "result": {"content": []}}
    assert gw.parse_mcp_response(stream)["id"] == 7


def test_a_reply_without_a_result_is_an_error(monkeypatch):
    c = gw.McpClient("http://gateway/mcp")
    monkeypatch.setattr(c, "_connect", lambda: "s")

    class R:
        status_code = 200
        text = 'data: {"jsonrpc":"2.0","method":"notifications/tools/list_changed"}\n\n'

        def raise_for_status(self):
            pass
    monkeypatch.setattr(c, "_post", lambda body, session: R())
    with pytest.raises(RuntimeError, match="no result"):
        c.call("catalogue__sync")


def test_the_toolbox_will_not_call_an_admin_tool(monkeypatch):
    import asyncio

    from canopy import toolbox
    monkeypatch.setattr(toolbox._client, "call", lambda *a: pytest.fail("reached the gateway"))
    for name in ("catalogue__embed", "mcp__canopy__catalogue__sync"):
        assert "control API" in asyncio.run(toolbox.call_tool(name, {"dry_run": False}))[0].text


def test_the_toolbox_does_not_list_an_admin_tool(monkeypatch):
    from canopy import toolbox
    monkeypatch.setattr(toolbox._client, "tools", lambda: [{"name": "catalogue__embed"}, {"name": "a__b"}])
    toolbox._cache.update(tools=None, at=0.0)
    assert [t["name"] for t in toolbox._all_tools()] == ["a__b"]
    toolbox._cache.update(tools=None, at=0.0)

