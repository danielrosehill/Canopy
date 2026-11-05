#!/usr/bin/env python3
"""Move the MCPJungle pin to a tag that has passed the contract suite, in both modes.

Usage: scripts/bump-mcpjungle.py 0.4.8-stdio
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def main(tag: str) -> None:
    path = ROOT / "compat.json"
    doc = json.loads(path.read_text())
    old = doc["mcpjungle"]["pinned"]
    doc["mcpjungle"]["pinned"] = tag
    doc["mcpjungle"]["tested"].insert(0, {
        "tag": tag, "version": "v" + tag.split("-")[0], "date": datetime.date.today().isoformat(),
        "modes": ["enterprise", "development"]})
    path.write_text(json.dumps(doc, indent=2) + "\n")
    for name in ("compose.yaml", ".env.example"):
        p = ROOT / name
        p.write_text(re.sub(rf"(MCPJUNGLE_TAG[:=-]+){re.escape(old)}", rf"\g<1>{tag}", p.read_text()))


if __name__ == "__main__":
    main(sys.argv[1])
