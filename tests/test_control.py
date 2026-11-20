import pytest

from canopy import control as seed


def test_expand_replaces_nested_values():
    out = seed.expand({"a": "x-${T}", "b": ["${T}"], "c": 1}, {"T": "v"})
    assert out == {"a": "x-v", "b": ["v"], "c": 1}


def test_expand_refuses_a_missing_variable():
    with pytest.raises(KeyError):
        seed.expand({"a": "${NOPE}"}, {})


def test_core_group_always_has_builtins():
    groups = seed.merged_groups([])
    assert groups[0]["name"] == "core"
    assert groups[0]["included_servers"] == ["catalogue", "toolbox"]


def test_user_core_group_keeps_its_members():
    groups = seed.merged_groups([{"name": "core", "included_tools": ["time__now"],
                                  "included_servers": ["extra"]}])
    assert groups[0]["included_servers"] == ["catalogue", "toolbox", "extra"]
    assert seed.group_servers(groups[0]) == {"catalogue", "toolbox", "extra", "time"}


def test_write_secret_is_private(tmp_path):
    p = tmp_path / "d" / "token"
    seed.write_secret(p, "abc")
    assert p.read_text() == "abc\n" and p.stat().st_mode & 0o077 == 0


# --- control, against a fake gateway ----------------------------------------------------

import json  # noqa: E402

import httpx  # noqa: E402

from canopy.control import Control, ControlError, Gateway  # noqa: E402


class FakeJungle:
    """Just enough of the gateway's REST API. `lie` accepts writes without doing them."""

    def __init__(self):
        self.servers, self.groups, self.lie = {}, {}, False
        self.refuse, self.time_out_group = set(), False   # server urls that will not connect
        self.tools, self.upstream = {}, {}   # what is registered, and what each upstream would offer

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/api/v0/servers" and method == "GET":
            return httpx.Response(200, json=list(self.servers.values()))
        if path == "/api/v0/tools":
            wanted = request.url.params.get("server")
            return httpx.Response(200, json=[t for t in self.tools.values()
                                             if not wanted or t["name"].startswith(f"{wanted}__")])
        if path == "/api/v0/tools/enable" or path == "/api/v0/tools/disable":
            self.tools[request.url.params["entity"]]["enabled"] = path.endswith("enable")
            return httpx.Response(200, json=[])
        if path.startswith("/api/v0/servers/") and method == "POST":
            _, name, verb = path.rsplit("/", 2)
            if not self.lie:
                self.servers[name]["enabled"] = verb == "enable"
            return httpx.Response(200, json={})
        if path == "/api/v0/server_configs":
            return httpx.Response(200, json=list(self.servers.values()))
        if path == "/api/v0/servers" and method == "POST":
            doc = json.loads(request.content)
            if doc.get("url") in self.refuse:
                return httpx.Response(502, text="cannot connect")
            if not self.lie:
                self.servers[doc["name"]] = doc
                for t in self.upstream.get(doc["name"], []):
                    self.tools[t] = {"name": t, "enabled": True}
            return httpx.Response(201, json={})
        if path.startswith("/api/v0/servers/") and method == "DELETE":
            if not self.lie:
                name = path.rsplit("/", 1)[1]
                self.servers.pop(name, None)
                self.tools = {k: v for k, v in self.tools.items() if not k.startswith(f"{name}__")}
            return httpx.Response(200, json={})
        if path == "/api/v0/tool-groups" and method == "POST":
            doc = json.loads(request.content)
            if self.time_out_group:
                self.time_out_group = False
                raise httpx.ReadTimeout("timed out")
            servers, excluded = set(doc.get("included_servers", [])), set(doc.get("excluded_tools", []))
            members = set(doc.get("included_tools", [])) | {
                t for t in self.tools if t.split("__")[0] in servers and t not in excluded}
            off = sorted(t for t in members if self.tools.get(t, {}).get("enabled") is False)
            if off:   # as the real gateway: see the contract suite
                return httpx.Response(400, json={"error": f"tool {off[0]} does not exist or is disabled"})
            self.groups[doc["name"]] = doc
            return httpx.Response(201, json={})
        if path.startswith("/api/v0/tool-groups/"):
            name = path.rsplit("/", 1)[1]
            if method == "DELETE":
                if not self.lie:
                    self.groups.pop(name, None)
                return httpx.Response(200, json={})
            return httpx.Response(200, json=self.groups[name]) if name in self.groups else httpx.Response(404)
        return httpx.Response(404)


@pytest.fixture
def jungle():
    return FakeJungle()


@pytest.fixture
def ctl(tmp_path, jungle):
    jungle.servers.update({"catalogue": {"name": "catalogue"}, "toolbox": {"name": "toolbox"}})
    (tmp_path / "config").mkdir()
    return Control(gateway=lambda token: Gateway(token, httpx.MockTransport(jungle)), sync=lambda: None,
                   config=tmp_path / "config", admin_dir=tmp_path / "admin", token_dir=tmp_path / "tokens",
                   mode="development", env={"KEY": "secret"})


def test_add_server_registers_expanded_and_stores_the_placeholder(ctl, jungle):
    raw = {"name": "x", "transport": "streamable_http", "url": "http://x", "bearer_token": "${KEY}"}
    ctl.add_server(raw)
    assert jungle.servers["x"]["bearer_token"] == "secret"
    assert ctl.read_config()["servers"] == [raw]


def test_add_server_refuses_a_missing_variable_and_writes_nothing(ctl, jungle):
    with pytest.raises(ControlError) as e:
        ctl.add_server({"name": "x", "bearer_token": "${NOPE}"})
    assert e.value.status == 400 and "x" not in jungle.servers
    assert ctl.read_config()["servers"] == []


def test_add_server_needs_replace_for_an_existing_name(ctl):
    ctl.add_server({"name": "x"})
    with pytest.raises(ControlError) as e:
        ctl.add_server({"name": "x"})
    assert e.value.status == 409
    ctl.add_server({"name": "x", "url": "http://new"}, replace=True)
    assert ctl.read_config()["servers"] == [{"name": "x", "url": "http://new"}]


def test_a_write_the_gateway_does_not_show_is_a_failure(ctl, jungle):
    jungle.lie = True
    with pytest.raises(ControlError) as e:
        ctl.add_server({"name": "x"})
    assert e.value.status == 502
    assert ctl.read_config()["servers"] == []


def test_a_delete_the_gateway_does_not_do_is_a_failure(ctl, jungle):
    ctl.add_server({"name": "x"})
    jungle.lie = True
    with pytest.raises(ControlError) as e:
        ctl.remove_server("x")
    assert e.value.status == 502
    assert ctl.read_config()["servers"] == [{"name": "x"}]


def test_builtins_and_the_core_group_cannot_be_removed(ctl):
    for call in (lambda: ctl.remove_server("catalogue"), lambda: ctl.remove_group("core")):
        with pytest.raises(ControlError) as e:
            call()
        assert e.value.status == 400


def test_a_server_in_a_group_cannot_be_removed(ctl):
    ctl.add_server({"name": "x"})
    ctl.set_group({"name": "g", "included_servers": ["x"]})
    with pytest.raises(ControlError) as e:
        ctl.remove_server("x")
    assert e.value.status == 409 and "g" in str(e.value)
    ctl.remove_group("g")
    ctl.remove_server("x")
    assert ctl.read_config() == {"servers": [], "groups": []}


@pytest.fixture
def applied(ctl, monkeypatch):
    """`a` and `b` registered through the config."""
    monkeypatch.setattr("canopy.control.wait_for_gateway", lambda: None)
    ctl.servers_path.write_text(json.dumps({"servers": [{"name": "a"}, {"name": "b"}], "groups": []}))
    assert ctl.apply()["ok"]
    return ctl


def write_servers(ctl, doc):
    ctl.servers_path.write_text(json.dumps(doc))


def test_apply_removes_a_server_dropped_from_the_config(applied, jungle):
    write_servers(applied, {"servers": [{"name": "a"}], "groups": []})
    out = applied.apply()
    assert out["ok"] and "removed b" in out["log"]
    assert "b" not in jungle.servers and "a" in jungle.servers
    assert "b" not in applied.read_state()


def test_apply_leaves_a_server_it_did_not_register(applied, jungle):
    jungle.servers["by-hand"] = {"name": "by-hand"}
    assert applied.apply()["ok"]
    assert "by-hand" in jungle.servers


def test_apply_never_touches_builtins(applied, jungle):
    write_servers(applied, {"servers": [{"name": "a"}], "groups": []})
    applied.apply()
    assert {"catalogue", "toolbox"} <= set(jungle.servers)


def test_a_missing_config_removes_nothing(applied, jungle):
    applied.servers_path.unlink()
    out = applied.apply()
    assert {"a", "b"} <= set(jungle.servers) and {"a", "b"} <= set(applied.read_state())
    assert "no servers in the config, so nothing was pruned" in out["log"]


def test_a_config_with_no_servers_removes_nothing(applied, jungle):
    write_servers(applied, {"groups": []})
    applied.apply()
    assert {"a", "b"} <= set(jungle.servers)


def test_an_unreadable_config_stops_apply_and_removes_nothing(applied, jungle):
    applied.servers_path.write_text('{"servers": [')
    with pytest.raises(ControlError, match="servers.json is not JSON") as e:
        applied.apply()
    assert e.value.status == 500
    assert {"a", "b"} <= set(jungle.servers)


def test_one_gateway_client_serves_every_call_with_the_same_token(applied):
    assert applied.gateway() is applied.gateway()


def test_a_dropped_server_still_in_a_group_stays_and_is_reported(applied, jungle):
    write_servers(applied, {"servers": [{"name": "a"}], "groups": [{"name": "g", "included_servers": ["b"]}]})
    out = applied.apply()
    assert "b" in jungle.servers and not out["ok"]
    assert any("still in group g" in f for f in out["failures"])


def test_a_failed_removal_is_reported_and_retried(applied, jungle):
    write_servers(applied, {"servers": [{"name": "a"}], "groups": []})
    jungle.lie = True
    out = applied.apply()
    assert not out["ok"] and "b" in applied.read_state()
    jungle.lie = False
    assert applied.apply()["ok"] and "b" not in jungle.servers


def test_a_group_naming_an_unknown_server_is_refused_and_not_saved(ctl):
    with pytest.raises(ControlError) as e:
        ctl.set_group({"name": "g", "included_servers": ["ghost"]})
    assert e.value.status == 400
    assert ctl.read_config()["groups"] == []


def test_names_cannot_be_paths(ctl):
    with pytest.raises(ControlError):
        ctl.remove_server("../clients/x")


def test_agent_token_is_none_in_development(ctl):
    assert ctl.agent_token() is None


# --- a registration that times out ------------------------------------------------------


def timing_out(tools: list[dict]) -> httpx.MockTransport:
    """The gateway as measured: the POST times out, the server is listed, tools may follow."""
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            raise httpx.ReadTimeout("slow upstream", request=request)
        if request.url.path == "/api/v0/tools":
            return httpx.Response(200, json=tools)
        return httpx.Response(200, json=[{"name": "x"}])
    return httpx.MockTransport(handle)


def test_register_accepts_a_timeout_once_the_tools_are_there():
    Gateway("", timing_out([{"name": "x__a"}])).register({"name": "x"})


def test_register_fails_if_the_tools_never_arrive(monkeypatch):
    monkeypatch.setattr("canopy.control.REGISTER_SETTLE", 0)
    with pytest.raises(RuntimeError, match="no tools arrived"):
        Gateway("", timing_out([{"name": "xy__a"}])).register({"name": "x"})


# --- a failed change keeps what worked ---------------------------------------------------

def test_a_replacement_that_fails_leaves_the_old_server(ctl, jungle):
    jungle.servers["up"] = {"name": "up", "url": "http://old"}
    jungle.refuse.add("http://new")
    with pytest.raises(ControlError) as e:
        ctl.add_server({"name": "up", "url": "http://new"}, replace=True)
    assert e.value.status == 502 and "restored" in str(e.value)
    assert jungle.servers["up"]["url"] == "http://old"


def test_apply_with_a_bad_edit_keeps_the_running_server_and_retries(ctl, jungle):
    jungle.servers["up"] = {"name": "up", "url": "http://old"}
    jungle.refuse.add("http://new")
    (ctl.config / "servers.json").write_text(json.dumps({"servers": [{"name": "up", "url": "http://new"}]}))
    g = ctl.gateway()
    changed, failures = ctl._register_all(g, ctl.read_config()["servers"], {}, lambda _: None)
    assert jungle.servers["up"]["url"] == "http://old" and "restored" in failures[0]
    jungle.refuse.clear()
    changed, failures = ctl._register_all(g, ctl.read_config()["servers"], {}, lambda _: None)
    assert changed and not failures and jungle.servers["up"]["url"] == "http://new"


def test_a_group_create_that_times_out_and_did_not_land_is_restored(ctl, jungle):
    jungle.servers["up"] = {"name": "up"}
    jungle.groups["g"] = {"name": "g", "included_servers": ["up"]}
    jungle.time_out_group = True
    with pytest.raises(RuntimeError, match="previous definition restored"):
        ctl.gateway().rebuild_group({"name": "g", "included_servers": ["catalogue"]}, {"up", "catalogue"})
    assert jungle.groups["g"]["included_servers"] == ["up"]


def test_a_group_failure_in_add_server_is_a_502_that_says_the_server_landed(ctl, jungle):
    (ctl.config / "servers.json").write_text(json.dumps({"groups": [{"name": "g", "included_servers": ["up"]}]}))
    jungle.time_out_group = True
    jungle.groups["g"] = {"name": "g", "included_servers": ["catalogue"]}
    with pytest.raises(ControlError) as e:
        ctl.add_server({"name": "up", "url": "http://x"})
    assert e.value.status == 502 and "is registered" in str(e.value)
    assert "up" in jungle.servers


def test_state_is_written_whole(ctl):
    ctl.write_state({"a": "1"})
    assert ctl.read_state() == {"a": "1"}
    assert [p.name for p in ctl.admin_dir.iterdir()] == ["registered.json"]


# --- alerts ------------------------------------------------------------------------------

def report(*failing):
    return {"components": {"gateway": {"ok": "gateway" not in failing}, "jobs": {"ok": "jobs" not in failing}}}


@pytest.fixture
def sent(ctl):
    out = []

    def post(url, timeout, json):
        out.append(json)
        return httpx.Response(200, request=httpx.Request("POST", url))
    ctl.alert_url, ctl.post = "http://hook", post
    return out


def test_an_outage_is_one_message_and_its_end_another(ctl, sent):
    for r in (report(), report("jobs"), report("jobs"), report()):
        ctl.alert(r)
    assert [m["text"] for m in sent] == ["Canopy: failing: jobs", "Canopy: recovered: jobs"]


def test_a_new_failure_during_an_outage_is_reported(ctl, sent):
    ctl.alert(report("jobs"))
    ctl.alert(report("gateway", "jobs"))
    assert sent[-1]["text"] == "Canopy: failing: gateway, jobs" and len(sent) == 2


def test_an_alert_that_cannot_be_sent_is_tried_again(ctl, sent):
    good = ctl.post

    def down(url, timeout, json):
        raise httpx.ConnectError("no")
    ctl.post = down
    ctl.alert(report("jobs"))
    ctl.post = good
    ctl.alert(report("jobs"))
    assert len(sent) == 1


def test_no_webhook_means_no_call(ctl):
    ctl.post = None
    ctl.alert_url = ""
    ctl.alert(report("jobs"))


# --- check before applying ---------------------------------------------------------------

def config(ctl, **doc):
    (ctl.config / "servers.json").write_text(json.dumps(doc))


def test_validate_finds_what_apply_would_trip_on(ctl):
    config(ctl, servers=[{"name": "a", "url": "${NOPE}"}, {"name": "a"}, {"name": "catalogue"}, {"name": "../x"}],
           groups=[{"name": "g", "included_servers": ["ghost"]}])
    problems = " | ".join(ctl.validate()["problems"])
    for word in ("NOPE", "named twice", "built in", "invalid name", "ghost"):
        assert word in problems


def test_validate_reads_a_broken_file_as_a_problem(ctl):
    (ctl.config / "servers.json").write_text("{")
    assert ctl.validate()["ok"] is False
    (ctl.config / "servers.json").write_text("[]")
    assert ctl.validate()["ok"] is False


def test_a_group_may_name_a_server_registered_by_hand(ctl, jungle):
    jungle.servers["hand"] = {"name": "hand"}
    config(ctl, groups=[{"name": "g", "included_servers": ["hand"]}])
    assert ctl.validate() == {"ok": True, "problems": []}


def test_diff_says_what_apply_would_do_and_keeps_values_out(ctl, jungle):
    jungle.servers |= {"old": {"name": "old"}, "held": {"name": "held"}, "hand": {"name": "hand"},
                       "same": {"name": "same", "url": "http://s"}}
    ctl.write_state({"old": "x", "held": "x", "same": seed.digest({"name": "same", "url": "http://s"}),
                     "catalogue": seed.digest(seed.BUILTIN_SERVERS[0]), "toolbox": seed.digest(seed.BUILTIN_SERVERS[1])})
    config(ctl, servers=[{"name": "same", "url": "http://s"}, {"name": "new", "url": "http://${KEY}"}],
           groups=[{"name": "g", "included_servers": ["held"]}])
    out = ctl.diff()
    assert out["register"] == [{"name": "new", "why": "not on the gateway"}]
    assert out["remove"] == ["old"] and out["blocked"] == {"held": "g"} and out["unmanaged"] == ["hand"]
    assert out["rebuild_groups"] == ["core", "g"]
    assert "secret" not in json.dumps(out)


def test_diff_after_apply_is_empty(ctl, jungle, monkeypatch):
    monkeypatch.setattr(seed, "wait_for_gateway", lambda: None)
    config(ctl, servers=[{"name": "a", "url": "http://a"}])
    assert ctl.apply()["ok"]
    out = ctl.diff()
    assert out["register"] == [] and out["remove"] == [] and out["rebuild_groups"] == []


# --- enable, disable and refresh ---------------------------------------------------------

def test_disable_is_read_back(ctl, jungle):
    jungle.servers["up"] = {"name": "up", "enabled": True}
    assert ctl.set_server_enabled("up", False)["enabled"] is False
    jungle.lie = True
    with pytest.raises(ControlError) as e:
        ctl.set_server_enabled("up", True)
    assert e.value.status == 502


def test_a_built_in_cannot_be_disabled_and_a_stranger_is_a_404(ctl):
    for name, status in (("catalogue", 400), ("ghost", 404)):
        with pytest.raises(ControlError) as e:
            ctl.set_server_enabled(name, False)
        assert e.value.status == status


def test_refresh_reports_the_change_and_keeps_what_was_disabled(ctl, jungle):
    jungle.servers["up"] = {"name": "up", "url": "http://u"}
    jungle.tools = {"up__a": {"name": "up__a", "enabled": False}, "up__gone": {"name": "up__gone", "enabled": True}}
    jungle.upstream["up"] = ["up__a", "up__new"]
    out = ctl.refresh_server("up")
    assert out["added"] == ["up__new"] and out["removed"] == ["up__gone"] and out["tools"] == 2
    assert jungle.tools["up__a"]["enabled"] is False and jungle.tools["up__new"]["enabled"] is True


def test_refresh_says_which_groups_took_the_new_tools(ctl, jungle):
    jungle.servers["up"] = {"name": "up", "url": "http://u"}
    jungle.tools = {"up__a": {"name": "up__a", "enabled": True}}
    jungle.upstream["up"] = ["up__a", "up__new"]
    write_servers(ctl, {"servers": [], "groups": [{"name": "all", "included_servers": ["up"]},
                                                  {"name": "one", "included_tools": ["up__a"]}]})
    out = ctl.refresh_server("up")
    assert out["joined_groups"] == {"all": ["up__new"]} and out["held_disabled"] == []


def test_new_tools_disabled_holds_what_a_refresh_finds(ctl, jungle):
    """2026-11-20: a refresh found 85 new tools on an upstream that had started re-exporting other
    services, and every group taking the whole server listed them at once."""
    jungle.servers["up"] = {"name": "up", "url": "http://u"}
    jungle.tools = {"up__a": {"name": "up__a", "enabled": True}}
    jungle.upstream["up"] = ["up__a", "up__new"]
    write_servers(ctl, {"servers": [{"name": "up", "url": "http://u", "new_tools": "disabled"}],
                        "groups": [{"name": "all", "included_servers": ["up"]}]})
    out = ctl.refresh_server("up")
    assert out["held_disabled"] == ["up__new"] and out["joined_groups"] == {}
    assert jungle.tools["up__new"]["enabled"] is False and jungle.tools["up__a"]["enabled"] is True
    assert jungle.groups["all"]["excluded_tools"] == ["up__new"]
    assert "new_tools" not in jungle.servers["up"]


def test_refresh_keeps_a_disabled_server_disabled(ctl, jungle):
    jungle.servers["up"] = {"name": "up", "enabled": False}
    ctl.refresh_server("up")
    assert jungle.servers["up"]["enabled"] is False


def test_the_refresh_job_carries_on_past_a_failure_and_then_says_so(ctl, jungle):
    jungle.servers |= {"bad": {"name": "bad", "url": "http://bad"}, "good": {"name": "good", "url": "http://g"},
                       "off": {"name": "off", "enabled": False}}
    jungle.refuse.add("http://bad")
    jungle.upstream["good"] = ["good__t"]
    with pytest.raises(RuntimeError, match="bad"):
        ctl.refresh_job()
    assert "good__t" in jungle.tools and "url" not in jungle.servers["off"]


# --- marketplaces and the catalogue ------------------------------------------------------

@pytest.fixture
def tools(ctl):
    calls = []

    def text(name, arguments=None):
        calls.append((name, arguments))
        return {"catalogue__list": '{"total": 4}'}.get(name, "{}")
    ctl._tool_text = text
    return calls


def test_a_new_marketplace_is_saved_and_gets_a_full_sync(ctl, tools):
    (ctl.config / "marketplaces.json").write_text(json.dumps({"marketplaces": [{"repo": "a/one"}]}))
    assert ctl.add_marketplace({"repo": "a/two"})["added"] == "two"
    assert ("catalogue__sync", {}) in tools
    assert [m["name"] for m in ctl.marketplaces()] == ["one", "two"]
    assert ctl.marketplaces()[0]["plugins"] == 4


@pytest.mark.parametrize("doc,status", [
    ({}, 400), ({"repo": "not a repo"}, 400), ({"repo": "a/b", "ref": "--upload-pack=x"}, 400),
    ({"repo": "a/b", "nope": 1}, 400), ({"repo": "x/one"}, 409)])
def test_a_bad_marketplace_changes_nothing(ctl, tools, doc, status):
    before = json.dumps({"marketplaces": [{"repo": "a/one"}]})
    (ctl.config / "marketplaces.json").write_text(before)
    with pytest.raises(ControlError) as e:
        ctl.add_marketplace(doc)
    assert e.value.status == status and (ctl.config / "marketplaces.json").read_text() == before


def test_removing_a_marketplace_syncs_and_a_stranger_is_a_404(ctl, tools):
    (ctl.config / "marketplaces.json").write_text(json.dumps({"marketplaces": [{"repo": "a/one", "name": "n"}]}))
    with pytest.raises(ControlError) as e:
        ctl.remove_marketplace("ghost")
    assert e.value.status == 404
    ctl.remove_marketplace("n")
    assert ctl.marketplaces() == [] and ("catalogue__sync", {}) in tools


def test_marketplaces_still_list_with_the_catalogue_down(ctl):
    (ctl.config / "marketplaces.json").write_text(json.dumps({"marketplaces": [{"repo": "a/one"}]}))

    def down(name, arguments=None):
        raise httpx.ConnectError("no")
    ctl._tool_text = down
    assert ctl.marketplaces() == [{"repo": "a/one", "name": "one", "plugins": None}]


def test_a_record_that_is_not_there_is_a_404(ctl):
    ctl._tool_text = lambda name, arguments=None: '=== x\n{"error":"no such record"}'
    with pytest.raises(ControlError) as e:
        ctl.read_record("x")
    assert e.value.status == 404


def test_embedding_is_a_dry_run_unless_asked(ctl, tools):
    ctl.embed_state()
    ctl.embed_state(True)
    assert [a for _, a in tools] == [{"dry_run": True}, {"dry_run": False}]


def test_core_keeps_admin_tools_from_agents_and_what_the_user_excluded():
    core = seed.merged_groups([{"name": "core", "excluded_tools": ["time__now"]}])[0]
    assert core["excluded_tools"] == ["time__now", "catalogue__sync", "catalogue__embed"]


def test_the_opt_out_hands_admin_tools_back(monkeypatch):
    monkeypatch.setattr(seed.gw, "ADMIN_TOOLS", ())
    assert "excluded_tools" not in seed.merged_groups([])[0]



def test_apply_refreshes_a_builtin_whose_tools_changed_with_an_update(applied, jungle, monkeypatch):
    served = {"http://catalogue:3000/mcp": ["search", "context_write"], "http://toolbox:3000/mcp": ["call_tool"]}

    class Client:
        def __init__(self, url, **_):
            self.url = url

        def tools(self):
            return [{"name": n} for n in served[self.url]]

    monkeypatch.setattr("canopy.control.gw.McpClient", Client)
    jungle.tools.update({"catalogue__search": {"name": "catalogue__search", "enabled": True},
                         "toolbox__call_tool": {"name": "toolbox__call_tool", "enabled": True}})
    jungle.upstream["catalogue"] = ["catalogue__search", "catalogue__context_write"]
    out = applied.apply()
    assert out["ok"] and "refreshed catalogue: +1 -0" in out["log"], out
    assert "catalogue__context_write" in jungle.tools
    assert "refreshed toolbox" not in out["log"]   # its list already matched
    assert "refreshed" not in applied.apply()["log"]   # and now nothing differs


# --- confirmation policy --------------------------------------------------------------

def test_annotations_decide_confirmation_with_mcp_defaults():
    assert seed.needs_confirmation({"readOnlyHint": True})[0] is False
    assert seed.needs_confirmation(None) == (True, "no annotations, so the MCP defaults apply: destructive, open world")
    assert seed.needs_confirmation({"readOnlyHint": False, "destructiveHint": True})[0] is True
    assert seed.needs_confirmation({"destructiveHint": False, "openWorldHint": True})[0] is True
    assert seed.needs_confirmation({"destructiveHint": False, "openWorldHint": False})[0] is False


def test_policy_lists_stay_out_of_the_gateway_and_decide_first(ctl, jungle):
    jungle.servers["mail"] = {"name": "mail"}
    jungle.tools.update({
        "mail__read": {"name": "mail__read", "enabled": True, "annotations": {"readOnlyHint": True}},
        "mail__send": {"name": "mail__send", "enabled": True},
        "mail__label": {"name": "mail__label", "enabled": True},
        "mail__purge": {"name": "mail__purge", "enabled": True, "annotations": {"readOnlyHint": True}},
        "mail__off": {"name": "mail__off", "enabled": False},
    })
    ctl.set_group({"name": "voice", "included_servers": ["mail"], "excluded_tools": ["mail__label"],
                   "confirm": ["mail__purge"], "no_confirm": []})
    assert "confirm" not in jungle.groups["voice"] and "no_confirm" not in jungle.groups["voice"]
    tools = {t["name"]: t for t in ctl.group_policy("voice")["tools"]}
    assert set(tools) == {"mail__read", "mail__send", "mail__purge"}
    assert tools["mail__read"]["confirm"] is False
    assert tools["mail__send"]["confirm"] is True
    assert tools["mail__purge"] == {"name": "mail__purge", "confirm": True, "why": "group confirm list",
                                    "annotations": {"readOnlyHint": True}}


def test_policy_for_an_unknown_group_is_a_404(ctl):
    with pytest.raises(ControlError) as e:
        ctl.group_policy("nope")
    assert e.value.status == 404


def test_validate_rejects_a_tool_both_confirmed_and_waived(ctl):
    write_servers(ctl, {"servers": [], "groups": [{"name": "g", "included_tools": ["toolbox__call_tool"],
                                                   "confirm": ["toolbox__call_tool"], "no_confirm": ["toolbox__call_tool"]}]})
    assert any("in both" in p for p in ctl.validate()["problems"])


# --- disabled tools and groups ----------------------------------------------------------

def test_a_disabled_tool_is_left_out_of_a_rebuilt_group_instead_of_breaking_it(ctl, jungle):
    jungle.servers["home"] = {"name": "home"}
    jungle.tools.update({"home__on": {"name": "home__on", "enabled": True},
                         "home__secret": {"name": "home__secret", "enabled": False}})
    ctl.set_group({"name": "house", "included_servers": ["home"]})
    assert jungle.groups["house"]["excluded_tools"] == ["home__secret"]
    ctl.set_group({"name": "pick", "included_tools": ["home__on", "home__secret"]})
    assert jungle.groups["pick"]["included_tools"] == ["home__on"]


def test_a_failed_create_restores_the_old_group_even_with_a_tool_disabled_since(ctl, jungle):
    jungle.servers["home"] = {"name": "home"}
    jungle.tools.update({"home__on": {"name": "home__on", "enabled": True},
                         "home__new": {"name": "home__new", "enabled": True}})
    ctl.set_group({"name": "house", "included_servers": ["home"]})
    jungle.tools["home__new"]["enabled"] = False
    jungle.time_out_group = True   # the create does not land, so the old definition has to go back
    with pytest.raises(ControlError, match="previous definition restored"):
        ctl.set_group({"name": "house", "included_servers": ["home"]})
    assert "house" in jungle.groups and jungle.groups["house"]["excluded_tools"] == ["home__new"]


def test_a_config_change_keeps_what_was_disabled(applied, jungle):
    """2026-11-20: changing a server's entry registered it again, which enabled the 77 tools disabled
    on it by hand. Only group exclusions kept them out of groups."""
    jungle.upstream["a"] = ["a__keep", "a__off"]
    write_servers(applied, {"servers": [{"name": "a", "description": "v2"}, {"name": "b"}], "groups": []})
    applied.apply()
    applied.set_tool_enabled("a__off", False)
    write_servers(applied, {"servers": [{"name": "a", "description": "v3"}, {"name": "b"}], "groups": []})
    out = applied.apply()
    assert "registered a" in out["log"] and out["ok"]
    assert jungle.tools["a__off"]["enabled"] is False and jungle.tools["a__keep"]["enabled"] is True


def test_disabled_tools_in_the_config_are_kept_off_on_every_apply(applied, jungle):
    jungle.upstream["a"] = ["a__keep", "a__dup"]
    write_servers(applied, {"servers": [{"name": "a", "disabled_tools": ["a__dup"]}, {"name": "b"}],
                            "groups": [{"name": "all", "included_servers": ["a"]}]})
    assert applied.apply()["ok"]
    assert jungle.tools["a__dup"]["enabled"] is False
    assert "disabled_tools" not in jungle.servers["a"]
    assert jungle.groups["all"]["excluded_tools"] == ["a__dup"]
    jungle.tools["a__dup"]["enabled"] = True   # turned on by hand: the next apply turns it off again
    applied.apply()
    assert jungle.tools["a__dup"]["enabled"] is False


def test_disabled_tools_must_name_the_servers_own_tools(ctl):
    write_servers(ctl, {"servers": [{"name": "a", "url": "http://a", "disabled_tools": ["b__x"]}], "groups": []})
    assert any("disabled_tools" in p for p in ctl.validate()["problems"])
