import pytest

from canopy import gateway as gw


def test_parse_plain_and_sse():
    assert gw.parse_mcp_response('{"id": 1}') == {"id": 1}
    assert gw.parse_mcp_response('event: message\ndata: {"id": 2}\n\n') == {"id": 2}
    assert gw.parse_mcp_response("") is None


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

