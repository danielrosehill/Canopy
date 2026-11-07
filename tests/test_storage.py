import json
from datetime import datetime, timezone

import httpx
import pytest
from starlette.testclient import TestClient

from canopy import api, storage
from canopy.control import Control, ControlError


def test_presign_matches_the_aws_documented_example():
    """The GET example from the SigV4 query-string documentation."""
    url = storage.presign("GET", "https://examplebucket.s3.amazonaws.com", "/test.txt", "AKIAIOSFODNN7EXAMPLE",
                          "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", 86400,
                          now=datetime(2013, 5, 24, tzinfo=timezone.utc))
    assert url.endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")


@pytest.mark.parametrize("given, want", [
    ("/home/me/report.pdf", "report.pdf"), ("a b.txt", "a_b.txt"), ("..", "file"), ("C:\\x\\y.txt", "y.txt"),
    ("דוח.pdf", "pdf"),
])
def test_safe_name(given, want):
    assert storage.safe_name(given) == want


def test_lifecycle_round_trip():
    assert storage.lifecycle_days(storage.lifecycle_xml(3)) == 3
    assert storage.lifecycle_days("<LifecycleConfiguration/>") is None


@pytest.fixture
def store(tmp_path):
    s = storage.Storage(secrets_dir=tmp_path / "s", config=tmp_path, admin_url="http://a", s3_url="http://s3",
                        public_url="http://public:9000")
    s.prepare()
    s.ready = json.dumps(s.buckets_config(), sort_keys=True)
    (s.dir / "s3.json").write_text(json.dumps({"key": "k", "secret": "x"}))
    s.sent = []

    def handler(request):
        s.sent.append(request)
        return httpx.Response(200, text="")
    s.http = httpx.Client(transport=httpx.MockTransport(handler))
    return s


def test_prepare_keeps_existing_secrets(store):
    before = (store.dir / "admin_token").read_text()
    store.prepare()
    assert (store.dir / "admin_token").read_text() == before
    assert len((store.dir / "rpc_secret").read_text().strip()) == 64
    assert (store.dir / "rpc_secret").stat().st_mode & 0o077 == 0


def test_stage_signs_for_the_audience_and_uploads_inside(store):
    out = store.stage(b"hi", "/x/a.txt", audience="remote")
    assert out["url"].startswith("http://public:9000/transient/") and out["key"].endswith("/a.txt")
    assert store.sent[0].url.host == "s3" and store.sent[0].content == b"hi"
    assert store.stage(b"hi", "a", audience="stack")["url"].startswith("http://s3/transient/")


@pytest.mark.parametrize("kw, status", [
    ({"audience": "mars"}, 400), ({"bucket": "nope"}, 404), ({"expires": 86401}, 400), ({"expires": 0}, 400),
])
def test_stage_refuses(store, kw, status):
    with pytest.raises(ControlError) as e:
        store.stage(b"", "a", **kw)
    assert e.value.status == status and not store.sent


def test_storage_json_names_the_buckets(store):
    store._layout = store._key = lambda: None
    store._bucket = lambda spec: None
    (store.config / "storage.json").write_text(json.dumps({"buckets": [{"name": "keep"}]}))
    assert store.stage(b"", "a", expires=storage.MAX_EXPIRES)["bucket"] == "keep"


def test_expiry_job_skips_an_unused_store(tmp_path):
    assert storage.Storage(secrets_dir=tmp_path).expiry_job() == "storage has not been used"


def test_check_off_is_fine_but_a_lost_store_is_not(store):
    def refused(_):
        raise httpx.ConnectError("no")
    store.http = httpx.Client(transport=httpx.MockTransport(refused))
    assert store.check()["ok"] is False
    (store.dir / "s3.json").unlink()
    assert store.check() == {"ok": True, "state": "off"}


class Stub(Control):
    def __init__(self):
        self.mode = "development"


AUTH = {"Authorization": "Bearer t"}


def test_stage_route(store):
    client = TestClient(api.create_app(Stub(), "t", storage=store), raise_server_exceptions=False)
    r = client.post("/v1/storage/stage?name=a.txt&expires=60", content=b"hi", headers=AUTH)
    assert r.status_code == 201 and r.json()["bytes"] == 2
    assert client.post("/v1/storage/stage", content=b"hi", headers=AUTH).status_code == 400
    assert client.post("/v1/storage/stage?name=a&expires=x", content=b"", headers=AUTH).status_code == 400
    assert client.post("/v1/storage/stage?name=a", content=b"hi").status_code == 401


def test_stage_route_enforces_the_size_limit(store, monkeypatch):
    monkeypatch.setattr(api, "MAX_BYTES", 1)
    client = TestClient(api.create_app(Stub(), "t", storage=store), raise_server_exceptions=False)
    assert client.post("/v1/storage/stage?name=a", content=b"hi", headers=AUTH).status_code == 413


def test_storage_off_says_so():
    client = TestClient(api.create_app(Stub(), "t"), raise_server_exceptions=False)
    assert client.get("/v1/storage/buckets", headers=AUTH).status_code == 503


def test_a_failed_upload_gives_no_url(store):
    """A 404 on the PUT used to pass as success, and the reply held a URL to nothing."""
    store.http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404, text="NoSuchBucket")))
    with pytest.raises(storage.ControlError) as e:
        store.stage(b"hi", "a.txt")
    assert e.value.status == 502 and store.ready is None


def test_a_bucket_added_while_running_is_made_before_use(store):
    made = []
    store._layout = store._key = lambda: None
    store._bucket = lambda spec: made.append(spec["name"])
    (store.config / "storage.json").write_text(json.dumps({"buckets": [{"name": "transient", "expire_days": 1},
                                                                       {"name": "keep"}]}))
    store.stage(b"hi", "a.txt", bucket="keep")
    assert made == ["transient", "keep"]
    store.stage(b"hi", "a.txt", bucket="keep")
    assert made == ["transient", "keep"]


def test_a_bucket_with_no_expiry_rule_still_reads_as_none(store):
    store.http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404, text="none")))
    assert store._rule("transient") is None
