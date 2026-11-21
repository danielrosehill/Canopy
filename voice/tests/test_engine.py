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


def schemas(*names):
    from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
    from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
    return ToolsSchema(standard_tools=[FunctionSchema(name=n, description=n, properties={}, required=[])
                                       for n in names])


def searching_group(loaded):
    from types import SimpleNamespace
    g = group()
    g.cfg = SimpleNamespace(tool_search_loaded=loaded)
    g.schema = schemas("home__lights_on", "mail__send", "calendar__events")
    return g


def test_with_tool_search_off_nothing_is_deferred_and_no_search_tool_is_sent():
    from canopy_voice.engine import DeferringAdapter
    from canopy_voice.gate import Gate, Policy
    tools = DeferringAdapter().to_provider_tools_format(searching_group(None).gated(Gate(Policy({}, "test"))))
    assert [t["name"] for t in tools] == ["calendar__events", "home__lights_on", "mail__send"]
    assert not any("defer_loading" in t for t in tools)


def test_tool_search_defers_every_tool_no_pattern_keeps_loaded_and_puts_the_search_tool_first():
    from canopy_voice.engine import TOOL_SEARCH, DeferringAdapter
    from canopy_voice.gate import Gate, Policy
    tools = DeferringAdapter().to_provider_tools_format(searching_group(["home__*"]).gated(Gate(Policy({}, "t"))))
    assert tools[0] == TOOL_SEARCH
    deferred = {t["name"]: t.get("defer_loading", False) for t in tools[1:]}
    assert deferred == {"calendar__events": True, "home__lights_on": False, "mail__send": True}


def test_drop_thinking_keeps_everything_but_assistant_thinking():
    from canopy_voice.engine import drop_thinking
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "x", "signature": "s"},
                                              {"type": "tool_use", "id": "t1", "name": "fx", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "1"}]}]
    out = drop_thinking(msgs)
    assert out[0] == msgs[0] and out[2] == msgs[2]
    assert out[1]["content"] == [{"type": "tool_use", "id": "t1", "name": "fx", "input": {}}]


def test_each_turn_is_appended_to_the_turn_log_and_a_bad_path_does_not_raise(tmp_path):
    import json
    from types import SimpleNamespace
    from canopy_voice.engine import Engine
    e = Engine.__new__(Engine)
    e.cfg = SimpleNamespace(turn_log=str(tmp_path / "logs" / "turns.jsonl"))
    e.log_turn({"text": "what's the weather", "reply": "Sunny, 24."})
    e.log_turn({"text": "שלום", "reply": "hi"})
    lines = (tmp_path / "logs" / "turns.jsonl").read_text().splitlines()
    assert [json.loads(x)["text"] for x in lines] == ["what's the weather", "שלום"]
    e.cfg.turn_log = str(tmp_path / "logs" / "turns.jsonl" / "not-a-dir" / "x.jsonl")
    e.log_turn({"text": "x"})   # logged as a warning, not raised
    e.cfg.turn_log = ""
    e.log_turn({"text": "x"})   # off
