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

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/api/v0/servers" and method == "GET":
            return httpx.Response(200, json=list(self.servers.values()))
        if path == "/api/v0/server_configs":
            return httpx.Response(200, json=list(self.servers.values()))
        if path == "/api/v0/servers" and method == "POST":
            doc = json.loads(request.content)
            if doc.get("url") in self.refuse:
                return httpx.Response(502, text="cannot connect")
            if not self.lie:
                self.servers[doc["name"]] = doc
            return httpx.Response(201, json={})
        if path.startswith("/api/v0/servers/") and method == "DELETE":
            if not self.lie:
                self.servers.pop(path.rsplit("/", 1)[1], None)
            return httpx.Response(200, json={})
        if path == "/api/v0/tool-groups" and method == "POST":
            doc = json.loads(request.content)
            if self.time_out_group:
                self.time_out_group = False
                raise httpx.ReadTimeout("timed out")
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
    with pytest.raises(json.JSONDecodeError):
        applied.apply()
    assert {"a", "b"} <= set(jungle.servers)


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
