"""The control API. Skills, the `canopy` CLI and the seed are all clients of this.

Every route except /health needs the API token. That token is separate from the gateway's
identities, and is required in development mode too. See docs/design.md.
"""
from __future__ import annotations

import hmac
import os
import secrets
import threading
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import scheduler as sched
from .backup import Backups
from . import store
from .control import Control, ControlError, write_secret
from .embedder import Embedder, EmbedError
from .storage import DEFAULT_EXPIRES, MAX_BYTES, Storage

API_DIR = Path(os.environ.get("CANOPY_API_DIR", "/api"))
PORT = int(os.environ.get("CANOPY_API_PORT", "8090"))


CATALOGUE_URL = os.environ.get("CATALOGUE_URL", "http://catalogue:3000")


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


def create_app(control: Control, token: str, embedder: Embedder | None = None,
               scheduler: sched.Scheduler | None = None, storage: Storage | None = None) -> Starlette:
    embedder = embedder or Embedder(primary="", fallback="")

    def need_storage() -> Storage:
        if storage is None:
            raise ControlError(503, "storage is not set up")
        return storage

    async def stage(request: Request) -> JSONResponse:
        """The body is the file. `name` is required; `bucket`, `audience` and `expires` are not."""
        q, s = request.query_params, need_storage()
        if not q.get("name"):
            raise ControlError(400, "needs `name`")
        try:
            expires = int(q.get("expires", DEFAULT_EXPIRES))
        except ValueError:
            raise ControlError(400, "`expires` is a number of seconds") from None
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > MAX_BYTES:
                raise ControlError(413, f"over the {MAX_BYTES // 2**20} MB limit, CANOPY_STAGE_MAX_MB")
        return await run(request, s.stage, bytes(data), q["name"], q.get("bucket"),
                         q.get("audience", "remote"), expires, request.headers.get("content-type"),
                         status=201)

    async def list_buckets(request: Request) -> JSONResponse:
        return await run(request, need_storage().buckets)

    def need_scheduler() -> sched.Scheduler:
        if scheduler is None:
            raise ControlError(503, "the scheduler is not running")
        return scheduler

    async def list_jobs(request: Request) -> JSONResponse:
        return await run(request, need_scheduler().listing)

    async def job_runs(request: Request) -> JSONResponse:
        return await run(request, need_scheduler().runs, request.path_params["name"])

    async def run_job(request: Request) -> JSONResponse:
        """Starts the job and returns: a sync can take minutes. Poll /v1/jobs/{name}/runs."""
        name, s = request.path_params["name"], need_scheduler()
        if name not in s.actions or name not in s.jobs():
            raise ControlError(404, f"no such job: {name}")
        threading.Thread(target=s.run, args=(name,), daemon=True).start()
        return JSONResponse({"job": name, "requested": True}, status_code=202)

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

    async def validate(request: Request) -> JSONResponse:
        return await run(request, control.validate)

    async def diff(request: Request) -> JSONResponse:
        return await run(request, control.diff)

    async def enable_server(request: Request) -> JSONResponse:
        return await run(request, control.set_server_enabled, request.path_params["name"], True)

    async def disable_server(request: Request) -> JSONResponse:
        return await run(request, control.set_server_enabled, request.path_params["name"], False)

    async def refresh_server(request: Request) -> JSONResponse:
        return await run(request, control.refresh_server, request.path_params["name"])

    async def enable_tool(request: Request) -> JSONResponse:
        return await run(request, control.set_tool_enabled, request.path_params["name"], True)

    async def disable_tool(request: Request) -> JSONResponse:
        return await run(request, control.set_tool_enabled, request.path_params["name"], False)

    def read_config(_: Request) -> JSONResponse:
        return JSONResponse(control.read_config())

    async def apply(request: Request) -> JSONResponse:
        return await run(request, control.apply)

    def agent_token(_: Request) -> JSONResponse:
        return JSONResponse({"token": control.agent_token(), "mode": control.mode})

    async def embeddings(request: Request) -> JSONResponse:
        doc = await body(request)
        texts = doc.get("input")
        texts = [texts] if isinstance(texts, str) else texts
        if not isinstance(doc.get("model"), str) or not isinstance(texts, list) or not texts:
            raise ControlError(400, "needs `model` and `input`")
        try:
            reply = await run_in_threadpool(embedder.embed, doc["model"], texts)
        except EmbedError as e:
            raise ControlError(e.status, str(e)) from None
        return JSONResponse(reply)

    async def skills_mcp(request: Request) -> Response:
        """The MCP Skills extension, forwarded to the catalogue. The gateway rejects these methods."""
        async with httpx.AsyncClient(timeout=60) as http:
            r = await http.post(f"{CATALOGUE_URL}/skills", content=await request.body(),
                                headers={"Content-Type": "application/json"})
        return Response(r.content, status_code=r.status_code, media_type="application/json")

    async def search(request: Request) -> Response:
        """The catalogue's search pipeline, for anything that is not an MCP client."""
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.post(f"{CATALOGUE_URL}/search", content=await request.body(),
                                headers={"Content-Type": "application/json"})
        return Response(r.content, status_code=r.status_code, media_type="application/json")

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
        Route("/v1/gateway/servers/{name}/enable", enable_server, methods=["POST"]),
        Route("/v1/gateway/servers/{name}/disable", disable_server, methods=["POST"]),
        Route("/v1/gateway/servers/{name}/refresh", refresh_server, methods=["POST"]),
        Route("/v1/gateway/groups", list_groups),
        Route("/v1/gateway/groups/{name}", set_group, methods=["PUT"]),
        Route("/v1/gateway/groups/{name}", remove_group, methods=["DELETE"]),
        Route("/v1/gateway/tools", list_tools),
        Route("/v1/gateway/tools/{name}/enable", enable_tool, methods=["POST"]),
        Route("/v1/gateway/tools/{name}/disable", disable_tool, methods=["POST"]),
        Route("/v1/config", read_config),
        Route("/v1/config/validate", validate),
        Route("/v1/config/diff", diff),
        Route("/v1/config/apply", apply, methods=["POST"]),
        Route("/v1/tokens/agent", agent_token),
        Route("/v1/embeddings", embeddings, methods=["POST"]),
        Route("/v1/search", search, methods=["POST"]),
        Route("/v1/skills/mcp", skills_mcp, methods=["POST"]),
        Route("/v1/storage/stage", stage, methods=["POST"]),
        Route("/v1/storage/buckets", list_buckets),
        Route("/v1/jobs", list_jobs),
        Route("/v1/jobs/{name}/runs", job_runs),
        Route("/v1/jobs/{name}/run", run_job, methods=["POST"]),
    ], exception_handlers={ControlError: control_error, httpx.HTTPError: gateway_error},
       middleware=[Middleware(RequireToken, token=token)])


def model_lookup(db: store.Db):
    def provider_model(model: str, provider: str) -> str:
        row = db.execute("SELECT provider_model FROM system.model_map WHERE model = %s AND provider = %s",
                         (model, provider)).fetchone()
        return row["provider_model"] if row else model
    return provider_model


def main() -> None:
    store.migrate()
    db, control, storage = store.Db(), Control(), Storage()
    storage.prepare()
    control.extra_checks["storage"] = storage.check
    jobs = sched.CatalogueJobs(db, control._tool_text)
    scheduler = sched.Scheduler(db, {"sync": jobs.sync, "sync-recent": jobs.sync_recent, "embed": jobs.embed, "health": control.health_job,
                                     "tool-refresh": control.refresh_job,
                                     **Backups(db).actions(), **storage.actions()})
    control.extra_checks["jobs"] = scheduler.check
    threading.Thread(target=scheduler.loop, daemon=True).start()
    app = create_app(control, load_token(), Embedder(provider_model=model_lookup(db)), scheduler, storage)
    uvicorn.run(app, host=os.environ.get("CANOPY_API_HOST", "0.0.0.0"), port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
