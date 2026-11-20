import json

import httpx
import jwt
import pytest
from starlette.testclient import TestClient

from canopy.control import federated
from canopy.signer import Signer, SignerError, create_app, load_entry

ENTRY = {"name": "google-workspace", "profiles": {"alex": "alex@example.com", "sam": "${SAM_EMAIL}"}}


def test_profiles_become_one_registration_each_and_identities_stay_out_of_them():
    out = federated([{"name": "plain", "url": "http://p"}, ENTRY])
    assert [s["name"] for s in out] == ["plain", "google-workspace-alex", "google-workspace-sam"]
    alex = out[1]
    assert alex["url"] == "http://google-workspace:8100/p/alex/mcp"
    assert alex["bearer_token"] == "${CANOPY_SIGNER_TOKEN}"
    assert "example.com" not in json.dumps(out)


def test_load_entry_expands_identities_from_the_environment(tmp_path):
    p = tmp_path / "servers.json"
    p.write_text(json.dumps({"servers": [ENTRY]}))
    assert load_entry("google-workspace", p, {"SAM_EMAIL": "sam@example.com"})["profiles"]["sam"] == "sam@example.com"
    with pytest.raises(SignerError, match="SAM_EMAIL"):
        load_entry("google-workspace", p, {})


@pytest.fixture
def app():
    seen = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b'{"ok": true}'),
                              headers={"mcp-session-id": "s1", "content-type": "application/json"})

    signer = Signer({**ENTRY, "profiles": {"alex": "alex@example.com"}}, "gw-token")
    return TestClient(create_app(signer, httpx.MockTransport(upstream))), signer, seen


def test_the_gateway_token_is_required_and_never_passed_on(app):
    client, signer, seen = app
    assert client.post("/p/alex/mcp", json={}).status_code == 401
    assert client.post("/p/alex/mcp", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/p/nobody/mcp", json={}, headers={"Authorization": "Bearer gw-token"}).status_code == 404
    r = client.post("/p/alex/mcp", json={"x": 1}, headers={"Authorization": "Bearer gw-token",
                                                          "x-pomerium-jwt-assertion": "forged"})
    assert r.status_code == 200 and r.headers["mcp-session-id"] == "s1"
    sent = seen[-1]
    assert "authorization" not in sent.headers
    assert sent.headers["x-pomerium-jwt-assertion"] != "forged"


def test_the_assertion_verifies_against_the_published_key(app):
    client, signer, seen = app
    client.post("/p/alex/mcp", json={}, headers={"Authorization": "Bearer gw-token"})
    token = seen[-1].headers["x-pomerium-jwt-assertion"]
    key = jwt.PyJWK(client.get("/jwks").json()["keys"][0]).key
    claims = jwt.decode(token, key, algorithms=["ES256"], audience="google-workspace")
    assert claims["email"] == "alex@example.com" and claims["exp"] - claims["iat"] == 60


def test_no_signer_token_refuses_to_start():
    with pytest.raises(SignerError):
        Signer(ENTRY, "")


def test_a_profile_can_carry_its_own_description():
    entry = {"name": "gw", "description": "Workspace",
             "profiles": {"work": {"identity": "alex@corp.example", "description": "Workspace: the WORK account only"},
                          "home": "alex@example.com"}}
    out = {s["name"]: s for s in federated([entry])}
    assert out["gw-work"]["description"] == "Workspace: the WORK account only"
    assert out["gw-home"]["description"] == "Workspace (profile home)"
    assert Signer(entry, "t").profiles == {"work": "alex@corp.example", "home": "alex@example.com"}
