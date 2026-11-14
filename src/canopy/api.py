"""The control API. Skills, the `canopy` CLI and the seed are all clients of this.

Every route except /health needs the API token. That token is separate from the gateway's
identities, and is required in development mode too. See docs/design.md.
"""
from __future__ import annotations

import hmac
import os
import secrets
import threading
from contextlib import asynccontextmanager
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
from .backup import BackupError, Backups
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
               scheduler: sched.Scheduler | None = None, storage: Storage | None = None,
               backups: Backups | None = None) -> Starlette:
    embedder = embedder or Embedder(primary="", fallback="")
    catalogue = httpx.AsyncClient(base_url=CATALOGUE_URL, timeout=60)   # shared: one pool, not one per request

    @asynccontextmanager
    async def lifespan(_):
        yield
        await catalogue.aclose()

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

    def start_job(name: str, follow: bool = True) -> JSONResponse:
        s = need_scheduler()
        if name not in s.actions or name not in s.jobs():
            raise ControlError(404, f"no such job: {name}")
        if s.running(name):
            raise ControlError(409, f"{name} is already running: poll /v1/jobs/{name}/runs")
        threading.Thread(target=s.run, args=(name, follow), daemon=True).start()
        return JSONResponse({"job": name, "requested": True}, status_code=202)

    async def run_job(request: Request) -> JSONResponse:
        """Starts the job and returns: a sync can take minutes. Poll /v1/jobs/{name}/runs."""
        return start_job(request.path_params["name"])

    def need_backups() -> Backups:
        if backups is None:
            raise ControlError(503, "backups are not set up")
        return backups

    async def list_backups(request: Request) -> JSONResponse:
        return await run(request, need_backups().snapshots)

    async def backup_config(request: Request) -> JSONResponse:
        return await run(request, need_backups().describe)

    async def backup_plan(request: Request) -> JSONResponse:
        return await run(request, need_backups().plan)

    async def run_backup(request: Request) -> JSONResponse:
        """`backup-data` alone. `canopy update` restarts the stack next, which would cut off a restore-test."""
        need_backups().describe()
        return start_job("backup-data", follow=False)

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

    async def list_marketplaces(request: Request) -> JSONResponse:
        return await run(request, control.marketplaces)

    async def add_marketplace(request: Request) -> JSONResponse:
        return await run(request, control.add_marketplace, await body(request), status=201)

    async def remove_marketplace(request: Request) -> JSONResponse:
        return await run(request, control.remove_marketplace, request.path_params["name"])

    async def catalogue_sync(request: Request) -> JSONResponse:
        doc = await body(request) if await request.body() else {}
        return await run(request, control.catalogue_sync, doc, status=202)

    async def read_record(request: Request) -> JSONResponse:
        return await run(request, control.read_record, request.path_params["id"])

    async def embeddings_pending(request: Request) -> JSONResponse:
        return await run(request, control.embed_state)

    async def embeddings_run(request: Request) -> JSONResponse:
        return await run(request, control.embed_state, True, status=202)

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

    async def forward(request: Request, path: str, timeout: float) -> Response:
        """Hand the body to the catalogue as it is. An unreachable catalogue is a 502 that says so."""
        try:
            r = await catalogue.post(path, content=await request.body(), timeout=timeout,
                                     headers={"Content-Type": "application/json"})
        except httpx.HTTPError as e:
            raise ControlError(502, f"catalogue: {type(e).__name__}: {e}") from None
        return Response(r.content, status_code=r.status_code, media_type="application/json")

    async def skills_mcp(request: Request) -> Response:
        """The MCP Skills extension, forwarded to the catalogue. The gateway rejects these methods."""
        return await forward(request, "/skills", 60)

    async def search(request: Request) -> Response:
        """The catalogue's search pipeline, for anything that is not an MCP client."""
        return await forward(request, "/search", 30)

    async def run(_: Request, fn, *args, status: int = 200) -> JSONResponse:
        return JSONResponse(await run_in_threadpool(fn, *args), status_code=status)

    async def control_error(_: Request, e: ControlError) -> JSONResponse:
        return JSONResponse({"error": str(e)}, status_code=e.status)

    async def backup_error(_: Request, e: BackupError) -> JSONResponse:
        return JSONResponse({"error": str(e)}, status_code=409)

    async def gateway_error(_: Request, e: httpx.HTTPError) -> JSONResponse:
        return JSONResponse({"error": f"gateway: {e}"}, status_code=502)

    async def unexpected(_: Request, e: Exception) -> JSONResponse:
        """Every reply is JSON with `error`, a fault in Canopy too, so clients have one shape to read."""
        return JSONResponse({"error": f"internal: {type(e).__name__}: {str(e)[:300]}"}, status_code=500)

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
        Route("/v1/catalogue/marketplaces", list_marketplaces),
        Route("/v1/catalogue/marketplaces", add_marketplace, methods=["POST"]),
        Route("/v1/catalogue/marketplaces/{name}", remove_marketplace, methods=["DELETE"]),
        Route("/v1/catalogue/sync", catalogue_sync, methods=["POST"]),
        Route("/v1/catalogue/records/{id:path}", read_record),
        Route("/v1/embeddings", embeddings, methods=["POST"]),
        Route("/v1/embeddings/pending", embeddings_pending),
        Route("/v1/embeddings/run", embeddings_run, methods=["POST"]),
        Route("/v1/search", search, methods=["POST"]),
        Route("/v1/skills/mcp", skills_mcp, methods=["POST"]),
        Route("/v1/storage/stage", stage, methods=["POST"]),
        Route("/v1/storage/buckets", list_buckets),
        Route("/v1/jobs", list_jobs),
        Route("/v1/jobs/{name}/runs", job_runs),
        Route("/v1/jobs/{name}/run", run_job, methods=["POST"]),
        Route("/v1/backup", list_backups),
        Route("/v1/backup", run_backup, methods=["POST"]),
        Route("/v1/backup/config", backup_config),
        Route("/v1/backup/plan", backup_plan),
    ], exception_handlers={ControlError: control_error, BackupError: backup_error, httpx.HTTPError: gateway_error,
                           Exception: unexpected},
       middleware=[Middleware(RequireToken, token=token)], lifespan=lifespan)


def model_lookup(db: store.Db):
    def provider_model(model: str, provider: str) -> str:
        row = db.execute("SELECT provider_model FROM system.model_map WHERE model = %s AND provider = %s",
                         (model, provider)).fetchone()
        return row["provider_model"] if row else model
    return provider_model


def main() -> None:
    store.migrate()
    db, control, storage = store.Db(), Control(), Storage()
    backups = Backups(db)
    storage.prepare()
    control.extra_checks["storage"] = storage.check
    jobs = sched.CatalogueJobs(db, control._tool_text)
    scheduler = sched.Scheduler(db, {"sync": jobs.sync, "sync-recent": jobs.sync_recent, "embed": jobs.embed, "health": control.health_job,
                                     "tool-refresh": control.refresh_job,
                                     **backups.actions(), **storage.actions()})
    control.extra_checks["jobs"] = scheduler.check
    # The jobs need the seed's tokens: wait for its first apply rather than a fixed delay alone.
    threading.Thread(target=scheduler.loop, args=(lambda: control.last_apply is not None,), daemon=True).start()
    app = create_app(control, load_token(), Embedder(provider_model=model_lookup(db)), scheduler, storage, backups)
    uvicorn.run(app, host=os.environ.get("CANOPY_API_HOST", "0.0.0.0"), port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
