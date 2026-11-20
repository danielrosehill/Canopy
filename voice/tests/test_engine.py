from canopy_voice.engine import GroupTools


class FakeMcp:
    def __init__(self):
        self.started = 0

    async def start(self):
        self.started += 1


class Wrapper:
    """Stands in for Pipecat's MCP handler: callable, with the cleanup that closes the connection."""
    def __init__(self, fail_first=False):
        self.calls, self.fail_first = 0, fail_first
        self._pipecat_cleanup = lambda: None

    async def __call__(self, params):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError("MCPClient is not connected. Use 'async with MCPClient(...)'")


def group():
    g = GroupTools.__new__(GroupTools)
    g.group, g.mcp = "voice_alex", FakeMcp()
    return g


async def test_the_handler_given_to_pipecat_hides_the_connection_cleanup():
    g, h = group(), Wrapper()
    wrapped = g.kept_open(h)
    assert not hasattr(wrapped, "_pipecat_cleanup")
    await wrapped(object())
    assert h.calls == 1 and g.mcp.started == 0


async def test_a_closed_connection_is_opened_again_and_the_call_runs_once_more():
    g, h = group(), Wrapper(fail_first=True)
    await g.kept_open(h)(object())
    assert g.mcp.started == 1 and h.calls == 2
