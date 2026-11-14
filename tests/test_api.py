import httpx
import pytest
from starlette.testclient import TestClient

from canopy import api
from canopy.control import Control, ControlError, Gateway


class Stub(Control):
    def __init__(self):
        self.mode = "development"

    def status(self):
        return {"ok": True, "components": {}}

    def add_server(self, raw, replace=False):
        if raw["name"] == "bad":
            raise ControlError(409, "nope")
        return {"registered": raw["name"], "replace": replace}

    def gateway(self):
        raise httpx.ConnectError("down")


@pytest.fixture
def client():
    return TestClient(api.create_app(Stub(), "right"), raise_server_exceptions=False)


AUTH = {"Authorization": "Bearer right"}


def test_health_is_open(client):
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "right2"}])
def test_every_other_route_needs_the_token(client, headers):
    assert client.get("/v1/status", headers=headers).status_code == 401
    assert client.post("/v1/config/apply", headers=headers).status_code == 401


def test_status_with_the_token(client):
    assert client.get("/v1/status", headers=AUTH).json()["ok"] is True


def test_add_server_passes_replace(client):
    r = client.post("/v1/gateway/servers?replace=true", json={"name": "x"}, headers=AUTH)
    assert r.status_code == 201 and r.json() == {"registered": "x", "replace": True}


def test_control_errors_keep_their_status(client):
    r = client.post("/v1/gateway/servers", json={"name": "bad"}, headers=AUTH)
    assert r.status_code == 409 and r.json() == {"error": "nope"}


def test_a_body_that_is_not_json_is_a_400(client):
    r = client.post("/v1/gateway/servers", content="x", headers=AUTH)
    assert r.status_code == 400


def test_gateway_failures_are_a_502(client):
    assert client.get("/v1/gateway/servers", headers=AUTH).status_code == 502


def test_token_is_made_once_and_kept(tmp_path, monkeypatch):
    monkeypatch.delenv("CANOPY_API_TOKEN", raising=False)
    first = api.load_token(tmp_path)
    assert first == api.load_token(tmp_path)
    assert (tmp_path / "token").stat().st_mode & 0o077 == 0


def test_env_token_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("CANOPY_API_TOKEN", "fixed")
    assert api.load_token(tmp_path) == "fixed"
    assert not (tmp_path / "token").exists()


def test_gateway_class_is_the_default():
    assert Control.__init__.__defaults__[0] is Gateway


def test_jobs_say_so_when_no_scheduler_is_running(client):
    assert client.get("/v1/jobs", headers=AUTH).status_code == 503


def test_running_an_unknown_job_is_a_404(tmp_path):
    from canopy.scheduler import Scheduler
    c = TestClient(api.create_app(Stub(), "right", scheduler=Scheduler(None, {}, tmp_path)),
                   raise_server_exceptions=False)
    assert c.post("/v1/jobs/nope/run", headers=AUTH).status_code == 404
    assert c.post("/v1/jobs/nope/run").status_code == 401


def test_search_forwards_to_the_catalogue_and_keeps_its_status(monkeypatch):
    seen = {}

    class Http:
        def __init__(self, **kw): pass
        async def aclose(self): pass
        async def post(self, url, content, headers, timeout):
            seen.update(url=url, body=content)
            return httpx.Response(400, content=b'{"error":"needs `query`"}')

    monkeypatch.setattr(api.httpx, "AsyncClient", Http)
    client = TestClient(api.create_app(Stub(), "right"), raise_server_exceptions=False)
    r = client.post("/v1/search", headers={"Authorization": "Bearer right"}, json={"limit": 1})
    assert r.status_code == 400 and r.json() == {"error": "needs `query`"}
    assert seen["url"].endswith("/search") and b"limit" in seen["body"]
    assert client.post("/v1/search", json={"query": "x"}).status_code == 401


def test_an_unreachable_catalogue_is_a_502_that_names_it(monkeypatch):
    class Http:
        def __init__(self, **kw): pass
        async def aclose(self): pass
        async def post(self, *a, **kw): raise httpx.ConnectError("refused")

    monkeypatch.setattr(api.httpx, "AsyncClient", Http)
    client = TestClient(api.create_app(Stub(), "right"), raise_server_exceptions=False)
    r = client.post("/v1/search", headers=AUTH, json={"query": "x"})
    assert r.status_code == 502 and r.json()["error"].startswith("catalogue: ConnectError")


def test_an_unexpected_fault_is_json_with_an_error(client, monkeypatch):
    def broken(self):
        raise KeyError("jobs")
    monkeypatch.setattr(Stub, "validate", broken, raising=False)
    r = client.get("/v1/config/validate", headers=AUTH)
    assert r.status_code == 500 and r.json()["error"].startswith("internal: KeyError")


def test_a_record_id_keeps_its_slashes(client, monkeypatch):
    seen = []
    monkeypatch.setattr(Stub, "read_record", lambda self, rid: seen.append(rid) or {"id": rid}, raising=False)
    assert client.get("/v1/catalogue/records/skill:p/name/references/x.md", headers=AUTH).status_code == 200
    assert seen == ["skill:p/name/references/x.md"]


def test_sync_takes_an_empty_body(client, monkeypatch):
    monkeypatch.setattr(Stub, "catalogue_sync", lambda self, doc: {"doc": doc}, raising=False)
    r = client.post("/v1/catalogue/sync", headers=AUTH)
    assert r.status_code == 202 and r.json() == {"doc": {}}


def test_backup_with_no_target_is_a_409_and_starts_nothing(tmp_path):
    from canopy.backup import Backups
    from canopy.scheduler import Scheduler
    c = TestClient(api.create_app(Stub(), "right", scheduler=Scheduler(None, {}, tmp_path),
                                  backups=Backups(None, config=tmp_path)), raise_server_exceptions=False)
    r = c.post("/v1/backup", headers=AUTH)
    assert r.status_code == 409 and "no backup target" in r.json()["error"]
    assert c.get("/v1/backup/config", headers=AUTH).status_code == 409


def test_backup_says_so_when_it_is_not_set_up(client):
    assert client.get("/v1/backup", headers=AUTH).status_code == 503

