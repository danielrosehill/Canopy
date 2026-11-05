"""The control API. Skills, the `canopy` CLI and the seed are all clients of this.

Every route except /health needs the API token. That token is separate from the gateway's
identities, and is required in development mode too. See docs/design.md.
"""
from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .control import Control, ControlError, write_secret

API_DIR = Path(os.environ.get("CANOPY_API_DIR", "/api"))
PORT = int(os.environ.get("CANOPY_API_PORT", "8090"))


def load_token(api_dir: Path = API_DIR) -> str:
    """CANOPY_API_TOKEN if set. Otherwise one is made on first start and kept."""
    token = os.environ.get("CANOPY_API_TOKEN", "")
    if token:
        return token
    path = api_dir / "token"
    if not (path.exists() and path.read_text().strip()):
        write_secret(path, secrets.token_urlsafe(32))
    return path.read_text().strip()


class RequireToken(BaseHTTPMiddleware):
    def __init__(self, app, token: str):
        super().__init__(app)
        self.token = token.encode()

    async def dispatch(self, request: Request, call_next):
        if request.url.path != "/health":
            given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(given.encode(), self.token):
                return JSONResponse({"error": "missing or wrong API token"}, status_code=401)
        return await call_next(request)


def create_app(control: Control, token: str) -> Starlette:
    async def body(request: Request) -> dict:
        try:
            doc = await request.json()
        except ValueError:
            raise ControlError(400, "the body must be JSON") from None
        if not isinstance(doc, dict):
            raise ControlError(400, "the body must be a JSON object")
        return doc

    def health(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    def status(_: Request) -> JSONResponse:
        return JSONResponse(control.status())

    def list_servers(_: Request) -> JSONResponse:
        return JSONResponse(control.gateway().servers())

    def list_groups(_: Request) -> JSONResponse:
        return JSONResponse(control.gateway().groups())

    def list_tools(_: Request) -> JSONResponse:
        return JSONResponse(control.gateway().tools())

    async def add_server(request: Request) -> JSONResponse:
        doc = await body(request)
        replace = request.query_params.get("replace") == "true"
        return await run(request, control.add_server, doc, replace, status=201)

    async def remove_server(request: Request) -> JSONResponse:
        return await run(request, control.remove_server, request.path_params["name"])

    async def set_group(request: Request) -> JSONResponse:
        doc = await body(request)
        doc["name"] = request.path_params["name"]
        return await run(request, control.set_group, doc)

    async def remove_group(request: Request) -> JSONResponse:
        return await run(request, control.remove_group, request.path_params["name"])

    def read_config(_: Request) -> JSONResponse:
        return JSONResponse(control.read_config())

    async def apply(request: Request) -> JSONResponse:
        return await run(request, control.apply)

    def agent_token(_: Request) -> JSONResponse:
        return JSONResponse({"token": control.agent_token(), "mode": control.mode})

    async def run(_: Request, fn, *args, status: int = 200) -> JSONResponse:
        return JSONResponse(await run_in_threadpool(fn, *args), status_code=status)

    async def control_error(_: Request, e: ControlError) -> JSONResponse:
        return JSONResponse({"error": str(e)}, status_code=e.status)

    async def gateway_error(_: Request, e: httpx.HTTPError) -> JSONResponse:
        return JSONResponse({"error": f"gateway: {e}"}, status_code=502)

    return Starlette(routes=[
        Route("/health", health),
        Route("/v1/status", status),
        Route("/v1/gateway/servers", list_servers),
        Route("/v1/gateway/servers", add_server, methods=["POST"]),
        Route("/v1/gateway/servers/{name}", remove_server, methods=["DELETE"]),
        Route("/v1/gateway/groups", list_groups),
        Route("/v1/gateway/groups/{name}", set_group, methods=["PUT"]),
        Route("/v1/gateway/groups/{name}", remove_group, methods=["DELETE"]),
        Route("/v1/gateway/tools", list_tools),
        Route("/v1/config", read_config),
        Route("/v1/config/apply", apply, methods=["POST"]),
        Route("/v1/tokens/agent", agent_token),
    ], exception_handlers={ControlError: control_error, httpx.HTTPError: gateway_error},
       middleware=[Middleware(RequireToken, token=token)])


def main() -> None:
    app = create_app(Control(), load_token())
    uvicorn.run(app, host=os.environ.get("CANOPY_API_HOST", "0.0.0.0"), port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
