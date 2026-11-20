"""canopy-voice serve | tools <speaker> | ask <speaker> "text" ["follow-up" ...]"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from loguru import logger

from . import config


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="canopy-voice")
    ap.add_argument("--config", default=os.environ.get("VOICE_CONFIG", "/config/voice.yaml"))
    ap.add_argument("--log-level", default=os.environ.get("VOICE_LOG_LEVEL", "INFO"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="the HTTP API")
    t = sub.add_parser("tools", help="a speaker's tools and which need confirmation")
    t.add_argument("speaker")
    a = sub.add_parser("ask", help="one conversation from the command line, text in and out")
    a.add_argument("speaker")
    a.add_argument("text", nargs="+")
    args = ap.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level=args.log_level)
    cfg = config.load(args.config)
    if args.cmd == "serve":
        from .server import serve
        serve(cfg)
        return
    asyncio.run(_offline(cfg, args))


async def _offline(cfg, args) -> None:
    from .engine import Engine
    speaker = next((s for s in cfg.speakers if s.id == args.speaker), None)
    if speaker is None:
        sys.exit(f"no speaker {args.speaker}; have {[s.id for s in cfg.speakers]}")
    engine = Engine(cfg)
    await engine.start()
    group = engine.groups[speaker.group]
    if args.cmd == "tools":
        out = [{"name": t.name, "confirm": group.policy.needs(t.name)} for t in group.schema.standard_tools]
        print(json.dumps({"group": speaker.group, "policy": group.policy.source, "tools": out}, indent=2))
        return
    cid = None
    for text in args.text:
        r = await engine.turn(speaker, text, cid)
        cid = r["conversation_id"]
        print(json.dumps({"said": text, **r}, ensure_ascii=False, indent=2))
