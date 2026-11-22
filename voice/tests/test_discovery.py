import json
from types import SimpleNamespace

from canopy_voice import discovery
from canopy_voice.engine import GroupTools
from canopy_voice.gate import Gate, Policy

GROUP = {"lights__on": {"properties": {"room": {"type": "string"}}, "required": ["room"]},
         "chores__list": {"properties": {}, "required": []},
         "catalogue__search": {"properties": {"query": {"type": "string"}}, "required": ["query"]}}


def search_result(*ids):
    return json.dumps({"results": [{"id": i, "description": i, "call": "toolbox__call_tool"} for i in ids],
                       "confidence": 0.7})


def test_found_tools_outside_the_group_are_dropped_and_skills_pass_through():
    out = json.loads(discovery.rewrite_search(
        search_result("tool:secrets__read", "tool:chores__list", "skill:house/chores"), GROUP, {"lights__on"}))
    assert [r["id"] for r in out["results"]] == ["tool:chores__list", "skill:house/chores"]
    assert out["confidence"] == 0.7


def test_an_unloaded_tool_is_called_through_call_tool_and_carries_its_schema():
    out = json.loads(discovery.rewrite_search(search_result("tool:lights__on"), GROUP, set()))
    item = out["results"][0]
    assert item["call"] == "call_tool"
    assert item["input_schema"] == {"type": "object", "properties": {"room": {"type": "string"}},
                                    "required": ["room"]}
    assert "note" in out


def test_a_loaded_tool_is_called_by_its_own_name_with_no_schema_added():
    out = json.loads(discovery.rewrite_search(search_result("tool:lights__on"), GROUP, {"lights__on"}))
    assert out["results"][0]["call"] == "lights__on" and "input_schema" not in out["results"][0]
    assert "note" not in out


def test_the_tools_section_is_narrowed_too_and_dropped_when_empty():
    text = json.dumps({"results": [{"id": "skill:a/b", "description": ""}],
                       "tools": [{"id": "tool:secrets__read", "description": ""}]})
    out = json.loads(discovery.rewrite_search(text, GROUP, set()))
    assert "tools" not in out and out["results"][0]["id"] == "skill:a/b"


def test_a_result_that_is_not_the_catalogues_json_is_left_alone():
    assert discovery.rewrite_search("Error calling mcp tool", GROUP, set()) == "Error calling mcp tool"


def test_call_checks():
    names, blocked = set(GROUP), {"catalogue__search"}
    assert discovery.check_call("chores__list", {}, names, blocked) is None
    assert "not available" in discovery.check_call("secrets__read", {}, names, blocked)
    assert "directly" in discovery.check_call("catalogue__search", {}, names, blocked)
    assert "needs `name`" in discovery.check_call("", {}, names, blocked)
    assert "object" in discovery.check_call("chores__list", ["x"], names, blocked)


# --- the engine: what is sent, and call_tool behind the gate ------------------------------------------

class Handler:
    """Stands in for Pipecat's MCP handler: records the tool it was asked to run."""
    def __init__(self, reply="done"):
        self.ran, self.reply = [], reply

    async def __call__(self, params):
        self.ran.append((params.function_name, params.arguments))
        await params.result_callback(self.reply)


def params(name, arguments):
    got = []

    async def cb(result, *a, **k):
        got.append(result)
    from pipecat.services.llm_service import FunctionCallParams
    p = FunctionCallParams(function_name=name, tool_call_id="t1", arguments=arguments, llm=None, pipeline_worker=None,
                           context=None, result_callback=cb)
    return p, got


def discovering_group(handlers, confirm=None):
    from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
    from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
    g = GroupTools.__new__(GroupTools)
    g.group, g.mcp = "voice_alex", None
    g.cfg = SimpleNamespace(tool_search_loaded=None, discovery_loaded=["lights__*"],
                            discovery_search="catalogue__search")
    g.schema = ToolsSchema(standard_tools=[FunctionSchema(name=n, description=n, properties=GROUP[n]["properties"],
                                                          required=GROUP[n]["required"], handler=handlers[n])
                                           for n in GROUP])
    g.kept_open = lambda h: h
    return g, Gate(Policy(confirm or {n: False for n in GROUP}, "test"))


def test_only_the_loaded_tools_the_search_and_call_tool_are_sent():
    g, gate = discovering_group({n: Handler() for n in GROUP})
    names = [t.name for t in g.gated(gate).standard_tools]
    assert names == ["catalogue__search", "lights__on", "call_tool"]


async def test_call_tool_runs_the_discovered_tool_by_its_own_name():
    hs = {n: Handler() for n in GROUP}
    g, gate = discovering_group(hs)
    call = next(t for t in g.gated(gate).standard_tools if t.name == "call_tool")
    p, got = params("call_tool", {"name": "chores__list", "arguments": {}})
    await call.handler(p)
    assert hs["chores__list"].ran == [("chores__list", {})] and got == ["done"]


async def test_call_tool_refuses_a_tool_outside_the_group():
    hs = {n: Handler() for n in GROUP}
    g, gate = discovering_group(hs)
    call = next(t for t in g.gated(gate).standard_tools if t.name == "call_tool")
    p, got = params("call_tool", {"name": "secrets__read", "arguments": {}})
    await call.handler(p)
    assert "not available" in got[0] and not any(h.ran for h in hs.values())


async def test_the_gate_holds_a_discovered_tool_under_its_own_name_and_runs_it_after_a_yes():
    hs = {n: Handler() for n in GROUP}
    g, gate = discovering_group(hs, confirm={"chores__list": True, "catalogue__search": False,
                                             "lights__on": False})
    call = next(t for t in g.gated(gate).standard_tools if t.name == "call_tool")
    p, got = params("call_tool", {"name": "chores__list", "arguments": {"x": 1}})
    await call.handler(p)
    assert hs["chores__list"].ran == [] and "call call_tool again" in got[0]
    assert gate.held.startswith("chores__list ")
    gate.hear("yes", ["yes"], ["no"])
    p, got = params("call_tool", {"name": "chores__list", "arguments": {"x": 1}})
    await call.handler(p)
    assert hs["chores__list"].ran == [("chores__list", {"x": 1})]


async def test_the_search_reply_is_narrowed_before_the_model_sees_it():
    hs = {n: Handler() for n in GROUP}
    hs["catalogue__search"] = Handler(search_result("tool:secrets__read", "tool:chores__list"))
    g, gate = discovering_group(hs)
    search = next(t for t in g.gated(gate).standard_tools if t.name == "catalogue__search")
    p, got = params("catalogue__search", {"query": "chores"})
    await search.handler(p)
    out = json.loads(got[0])
    assert [r["id"] for r in out["results"]] == ["tool:chores__list"] and out["results"][0]["call"] == "call_tool"


def test_config_takes_tool_discovery_and_a_speaker_search(tmp_path):
    from canopy_voice import config
    f = tmp_path / "voice.yaml"
    f.write_text("gateway: {url: http://gw}\ntool_discovery: {loaded: ['lights__*']}\n"
                 "speakers:\n  alex: {group: g1, token_env: T1}\n"
                 "  sam: {group: g2, token_env: T2, tool_discovery: {search: household__search}}\n")
    cfg = config.load(f, env={"T1": "a", "T2": "b"})
    assert cfg.discovery_loaded == ["lights__*"] and cfg.tool_search_loaded is None
    alex, sam = cfg.speakers
    assert cfg.search_tool(alex) == "catalogue__search" and cfg.search_tool(sam) == "household__search"


def test_config_refuses_both_ways_of_finding_tools(tmp_path):
    import pytest
    from canopy_voice import config
    f = tmp_path / "voice.yaml"
    f.write_text("gateway: {url: http://gw}\ntool_discovery: {loaded: []}\ntool_search: {loaded: []}\n"
                 "speakers:\n  alex: {group: g1, token_env: T1}\n")
    with pytest.raises(config.ConfigError):
        config.load(f, env={"T1": "a"})
