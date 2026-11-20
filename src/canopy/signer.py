"""The identity signer for a federated upstream (docs/federation.md).

One upstream serves several identities, and trusts a gateway in front of it to say whose request each
one is. This is that gateway end: it sits in the upstream's network namespace, so the upstream reads its
public key over loopback, and it forwards `/p/<profile>/mcp` to the upstream with a short-lived ES256
token naming the profile's identity. Only the gateway may call it: every request must carry
CANOPY_SIGNER_TOKEN, which Canopy registers each profile's upstream with.

    SIGNER_FOR=google-workspace python -m canopy.signer

The entry named by SIGNER_FOR in servers.json gives the profiles, and optionally a `signer` block:
`upstream` (default http://127.0.0.1:8000/mcp), `header` (default x-pomerium-jwt-assertion), `claim`
(default email), `audience` (default the entry's name), `ttl` seconds (default 60).
"""
from __future__ import annotations

import hmac
import json
import os
import time
import uuid
from pathlib import Path

import httpx
import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from .control import SIGNER_PORT, expand

CONFIG = Path(os.environ.get("CANOPY_CONFIG", "/config")) / "servers.json"
# Not passed on: hop-by-hop, the gateway's own credential, and any assertion a caller tried to supply.
DROP_IN = {"host", "content-length", "connection", "transfer-encoding", "authorization"}
DROP_OUT = {"content-length", "transfer-encoding", "connection"}


class SignerError(ValueError):
    pass


def load_entry(name: str, path: Path = CONFIG, env: dict | None = None) -> dict:
    doc = json.loads(path.read_text())
    entry = next((s for s in doc.get("servers", []) if s.get("name") == name and "profiles" in s), None)
    if entry is None:
        raise SignerError(f"no federated server {name!r} (an entry with `profiles`) in {path}")
    try:
        return expand(entry, dict(os.environ if env is None else env))
    except KeyError as e:
        raise SignerError(f"environment variable {e.args[0]} is not set") from None


class Signer:
    def __init__(self, entry: dict, token: str):
        if not token:
            raise SignerError("CANOPY_SIGNER_TOKEN is not set")
        opts = entry.get("signer", {})
        self.profiles: dict[str, str] = entry["profiles"]
        self.upstream = opts.get("upstream", "http://127.0.0.1:8000/mcp")
        self.header = opts.get("header", "x-pomerium-jwt-assertion").lower()
        self.claim = opts.get("claim", "email")
        self.audience = opts.get("audience", entry["name"])
        self.ttl = int(opts.get("ttl", 60))
        self.token = token.encode()
        self.key = ec.generate_private_key(ec.SECP256R1())   # new each start; the upstream refetches on a new kid
        self.kid = uuid.uuid4().hex[:12]
        self.jwk = {**json.loads(ECAlgorithm.to_jwk(self.key.public_key())), "kid": self.kid, "use": "sig", "alg": "ES256"}

    def assertion(self, identity: str) -> str:
        now = int(time.time())
        claims = {self.claim: identity, "sub": identity, "aud": self.audience, "iat": now, "exp": now + self.ttl}
        return jwt.encode(claims, self.key, algorithm="ES256", headers={"kid": self.kid})

    def allowed(self, request: Request) -> bool:
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip().encode()
        return hmac.compare_digest(given, self.token)


def create_app(signer: Signer, transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10), transport=transport)

    async def jwks(_: Request) -> JSONResponse:
        return JSONResponse({"keys": [signer.jwk]})

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "profiles": sorted(signer.profiles)})

    async def proxy(request: Request):
        if not signer.allowed(request):
            return JSONResponse({"error": "missing or wrong signer token"}, status_code=401)
        identity = signer.profiles.get(request.path_params["profile"])
        if identity is None:
            return JSONResponse({"error": "no such profile"}, status_code=404)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP_IN | {signer.header}}
        headers[signer.header] = signer.assertion(identity)
        upstream = client.build_request(request.method, signer.upstream, headers=headers,
                                        params=request.query_params, content=await request.body())
        try:
            r = await client.send(upstream, stream=True)
        except httpx.HTTPError as e:
            return JSONResponse({"error": f"upstream: {type(e).__name__}"}, status_code=502)
        out = {k: v for k, v in r.headers.items() if k.lower() not in DROP_OUT}
        return StreamingResponse(r.aiter_raw(), status_code=r.status_code, headers=out,
                                 background=BackgroundTask(r.aclose))

    return Starlette(routes=[
        Route("/jwks", jwks),
        Route("/health", health),
        Route("/p/{profile}/mcp", proxy, methods=["GET", "POST", "DELETE"]),
    ])


def main() -> None:
    entry = load_entry(os.environ["SIGNER_FOR"])
    signer = Signer(entry, os.environ.get("CANOPY_SIGNER_TOKEN", ""))
    print(f"signer for {entry['name']}: profiles {sorted(signer.profiles)}, upstream {signer.upstream}", flush=True)
    uvicorn.run(create_app(signer), host="0.0.0.0", port=SIGNER_PORT, log_level="warning")


if __name__ == "__main__":
    main()
