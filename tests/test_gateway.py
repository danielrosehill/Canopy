from canopy import gateway as gw


def test_parse_plain_and_sse():
    assert gw.parse_mcp_response('{"id": 1}') == {"id": 1}
    assert gw.parse_mcp_response('event: message\ndata: {"id": 2}\n\n') == {"id": 2}
    assert gw.parse_mcp_response("") is None
