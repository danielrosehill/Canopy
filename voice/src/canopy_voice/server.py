"""The HTTP API for thin wake-word clients. The bearer token says who is speaking.

    GET  /health                    {"ok", "groups": {name: {tools, confirm, policy}}}
    POST /v1/turn                   JSON {text, conversation_id?, speak?, client?}  -> JSON reply (+ audio_b64 WAV if speak)
    POST /v1/turn/audio             multipart audio (any format ffmpeg reads), conversation_id?, reply=wav|json
"""
from __future__ import annotations

import base64
import contextlib
from urllib.parse import quote

import uvicorn
from loguru import logger
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import speech
from .config import Config
from .engine import Engine


def create_app(cfg: Config, engine: Engine | None = None) -> Starlette:
    engine = engine or Engine(cfg)
    stt, tts = speech.build_stt(cfg.stt), speech.build_tts(cfg.tts)

    @contextlib.asynccontextmanager
    async def lifespan(_):
        if not engine.groups:
            await engine.start()
        yield

    def who(request: Request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        return cfg.speaker_for_token(token) if token else None

    def unauthorised() -> JSONResponse:
        return JSONResponse({"error": "missing or unknown token"}, status_code=401)

    async def speak(text: str) -> bytes:
        pcm, rate = await speech.synthesize(tts, text)
        return speech.pcm_to_wav(pcm, rate)

    def health(_: Request) -> JSONResponse:
        groups = {name: {"tools": len(g.schema.standard_tools) if g.schema else 0,
                         "confirm": sum(1 for t in (g.schema.standard_tools if g.schema else []) if g.policy.needs(t.name)),
                         "policy": g.policy.source}
                  for name, g in engine.groups.items()}
        return JSONResponse({"ok": bool(groups) and all(v["tools"] for v in groups.values()), "groups": groups})

    async def turn(request: Request) -> JSONResponse:
        speaker = who(request)
        if speaker is None:
            return unauthorised()
        try:
            body = await request.json()
            text = str(body["text"]).strip()
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"error": "body must be JSON with `text`"}, status_code=400)
        if not text:
            return JSONResponse({"error": "`text` is empty"}, status_code=400)
        out = await engine.turn(speaker, text, body.get("conversation_id"), client=str(body.get("client") or ""))
        if body.get("speak") and tts and out["reply"]:
            out["audio_b64"] = base64.b64encode(await speak(out["reply"])).decode()
        return JSONResponse(out)

    async def turn_audio(request: Request) -> Response:
        speaker = who(request)
        if speaker is None:
            return unauthorised()
        if stt is None:
            return JSONResponse({"error": "no `stt` configured"}, status_code=501)
        form = await request.form()
        upload = form.get("audio")
        if upload is None or isinstance(upload, str):
            return JSONResponse({"error": "multipart field `audio` is required"}, status_code=400)
        try:
            pcm = await speech.to_pcm16k(await upload.read())
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        transcript = await speech.transcribe(stt, pcm)
        if not transcript:
            return JSONResponse({"error": "heard nothing", "transcript": ""}, status_code=422)
        out = await engine.turn(speaker, transcript, form.get("conversation_id") or None,
                                client=str(form.get("client") or "audio"))
        out["transcript"] = transcript
        if form.get("reply") == "json" or tts is None or not out["reply"]:
            return JSONResponse(out)
        headers = {"X-Conversation-Id": out["conversation_id"], "X-Transcript": quote(transcript),
                   "X-Reply-Text": quote(out["reply"])}
        return Response(await speak(out["reply"]), media_type="audio/wav", headers=headers)

    return Starlette(routes=[
        Route("/health", health),
        Route("/v1/turn", turn, methods=["POST"]),
        Route("/v1/turn/audio", turn_audio, methods=["POST"]),
    ], lifespan=lifespan)


def serve(cfg: Config) -> None:
    logger.info(f"canopy-voice on {cfg.host}:{cfg.port}, speakers {[s.id for s in cfg.speakers]}")
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="warning")
