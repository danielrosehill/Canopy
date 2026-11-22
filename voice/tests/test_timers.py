import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from canopy_voice import timers as T

TZ = ZoneInfo("Europe/Berlin")
START = datetime(2026, 3, 2, 17, 0, tzinfo=TZ).timestamp()    # Monday 17:00 local


class Clock:
    def __init__(self):
        self.t = START

    def __call__(self):
        return self.t


def scheduler(tmp_path, ok=True, **kw):
    clock, sent = Clock(), []

    async def deliver(t):
        sent.append(t)
        return ok, "pushed" if ok else "Error calling mcp tool x: down"

    s = T.Scheduler(T.TimerStore(tmp_path / "timers.json"), TZ, deliver, clock=clock, **kw)
    return s, clock, sent


async def test_a_timer_fires_once_when_due_and_goes_to_history(tmp_path):
    s, clock, sent = scheduler(tmp_path)
    t = s.set_timer("alex", 60, "tea")
    assert await s.tick() <= 15 and sent == []
    clock.t += 60
    await s.tick()
    await s.tick()
    assert [x.id for x in sent] == [t.id]
    doc = s.store.read()
    assert doc["pending"] == [] and doc["history"][-1].status == "fired" and doc["history"][-1].result == "pushed"


async def test_timers_survive_a_restart(tmp_path):
    s, clock, _ = scheduler(tmp_path)
    s.set_timer("alex", 600, "oven")
    again, _, sent = scheduler(tmp_path)          # a new process reading the same file
    again.clock.t = clock.t + 600
    await again.tick()
    assert [x.text for x in sent] == ["oven"]


async def test_a_timer_missed_by_more_than_the_grace_is_not_delivered(tmp_path):
    s, clock, sent = scheduler(tmp_path, late_grace_s=300)
    s.set_timer("alex", 60)
    clock.t += 60 + 301
    await s.tick()
    assert sent == [] and s.store.read()["history"][-1].status == "missed"


async def test_a_failed_delivery_is_recorded(tmp_path):
    s, clock, _ = scheduler(tmp_path, ok=False)
    s.set_timer("alex", 5)
    clock.t += 5
    await s.tick()
    h = s.store.read()["history"][-1]
    assert h.status == "failed" and "down" in h.result


def test_timers_are_per_speaker_and_cancel_by_label_or_the_only_one(tmp_path):
    s, _, _ = scheduler(tmp_path)
    s.set_timer("alex", 60, "pasta")
    s.set_timer("alex", 120, "eggs")
    s.set_timer("sam", 60, "pasta")
    assert [t.text for t in s.pending("alex")] == ["pasta", "eggs"]
    assert s.cancel("alex") == []                       # two running, none named
    assert [t.text for t in s.cancel("alex", text="PASTA")] == ["pasta"]
    assert [t.speaker for t in s.pending("sam")] == ["sam"]  # Sam's pasta is untouched
    assert [t.text for t in s.cancel("alex")] == ["eggs"]   # the only one left


def test_reminder_times_are_local_and_hh_mm_means_the_next_one(tmp_path):
    s, _, _ = scheduler(tmp_path)
    now = datetime.fromtimestamp(START, TZ)
    assert T.parse_at("18:30", now) == datetime(2026, 3, 2, 18, 30, tzinfo=TZ)
    assert T.parse_at("09:00", now) == datetime(2026, 3, 3, 9, 0, tzinfo=TZ)
    assert T.parse_at("2026-03-04 07:15", now) == datetime(2026, 3, 4, 7, 15, tzinfo=TZ)
    t = s.set_reminder("alex", "18:30", "call the plumber")
    assert t.due == datetime(2026, 3, 2, 18, 30, tzinfo=TZ).timestamp()
    with pytest.raises(ValueError):
        s.set_reminder("alex", "2026-03-01 10:00", "past")
    with pytest.raises(ValueError):
        s.set_reminder("alex", "tomorrow-ish", "x")


def test_limits(tmp_path):
    s, _, _ = scheduler(tmp_path, max_seconds=3600)
    with pytest.raises(ValueError):
        s.set_timer("alex", 3601)
    with pytest.raises(ValueError):
        s.set_timer("alex", 0)


async def test_the_tool_results_say_what_happened_and_errors_do_not_raise(tmp_path):
    s, _, _ = scheduler(tmp_path)
    r = await T.call_tool(s, "alex", "set_timer", {"seconds": 90, "label": "tea"})
    assert r["ok"] and r["set"]["remaining"] == "1 minute 30 seconds"
    assert (await T.call_tool(s, "alex", "set_timer", {"seconds": "soon"}))["ok"] is False
    assert [t["text"] for t in (await T.call_tool(s, "alex", "list_timers", {}))["timers"]] == ["tea"]
    assert (await T.call_tool(s, "sam", "list_timers", {}))["timers"] == []
    r = await T.call_tool(s, "alex", "cancel_timer", {"all": True})
    assert r["ok"] and r["still_running"] == []
    json.dumps(r)


def test_delivery_arguments_are_templated():
    args = {"to": "{speaker}", "message": "{message}", "tier": "asap", "keep": "{unknown}", "n": 3, "l": ["{name}"]}
    out = T.fill(args, {"speaker": "alex", "message": "Timer done: tea", "name": "Alex"})
    assert out == {"to": "alex", "message": "Timer done: tea", "tier": "asap", "keep": "{unknown}", "n": 3,
                   "l": ["Alex"]}


def test_config_default_delivery_and_a_speaker_override(tmp_path):
    from canopy_voice.config import ConfigError, load
    (tmp_path / "v.yaml").write_text("""
gateway: {url: "http://gw"}
timers:
  store: /data/timers.json
  deliver: [{tool: notify__push, arguments: {to: "{speaker}", message: "{message}"}}]
speakers:
  alex: {group: g_alex, token_env: T_A}
  sam: {group: g_sam, token_env: T_S, timers: {deliver: {tool: sam_notify__push}}}
  kim: {group: g_kim, token_env: T_K, timers: {deliver: []}}
""")
    cfg = load(tmp_path / "v.yaml", {"T_A": "a", "T_S": "s", "T_K": "k"})
    by = {s.id: s for s in cfg.speakers}
    assert cfg.timers_store == "/data/timers.json"
    assert by["alex"].timer_deliver[0]["tool"] == "notify__push"
    assert by["sam"].timer_deliver == [{"tool": "sam_notify__push", "arguments": {}}]
    assert by["kim"].timer_deliver == []
    (tmp_path / "bad.yaml").write_text("gateway: {url: x}\ntimers: {deliver: [{arguments: {}}]}\nspeakers: {}\n")
    with pytest.raises(ConfigError):
        load(tmp_path / "bad.yaml", {})


async def test_the_engine_delivers_through_the_speakers_group_with_the_message(tmp_path):
    from types import SimpleNamespace
    from canopy_voice.config import Config, Speaker
    from canopy_voice.engine import Engine
    calls = []

    class Group:
        async def call(self, tool, arguments):
            calls.append((tool, arguments))
            return True, "paged"

    sam = Speaker(id="sam", name="Sam", group="g_sam", token="s",
                  timer_deliver=[{"tool": "notify__push", "arguments": {"to": "{speaker}", "message": "{message}"}}])
    cfg = Config(gateway_url="", policy_url="", policy_token="", gateway_token="", model="m", effort=None,
                 max_tokens=1, prompt="", timezone="UTC", session_ttl_s=1, stt={}, tts={}, speakers=[sam],
                 timers_store=str(tmp_path / "t.json"))
    e = Engine(cfg)
    e.groups["g_sam"] = Group()
    ok, result = await e.deliver_timer(T.Timer(id="x", speaker="sam", kind="timer", due=100, text="", created=40))
    assert ok and calls == [("notify__push", {"to": "sam", "message": "Your 1 minute timer is done"})]
    assert "notify__push: ok" in result
    names = [t.name for t in e.timer_tools(sam)]
    assert names == ["set_timer", "set_reminder", "list_timers", "cancel_timer"]
    assert e.timer_tools(SimpleNamespace(timer_deliver=[], id="kim")) == []
