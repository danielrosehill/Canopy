#!/usr/bin/env python3
"""Add Canopy's recommended snippet to a CLAUDE.md, between markers, so it can be updated or removed.

    snippet.py --show
    snippet.py [--target FILE] [--dry-run] [--remove]

Exit 0 on success or no change. Exit 1 with no write if the markers are broken. Standard library only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BEGIN, END = "<!-- canopy:begin -->", "<!-- canopy:end -->"
SNIPPET = """## Skills and tools: Canopy

Every skill, plugin and MCP tool I have is on my Canopy server. Most are not installed or listed here, so one
missing from your list still exists.

1. Before a task that might have a skill, call `catalogue__search` with the task in plain words.
2. Prefer a skill: `catalogue__read` its id and follow it.
3. For a tool not in your list, `catalogue__read` its `tool:` id for the schema, then call it with
   `toolbox__call_tool(name, arguments)`.
4. `weak: true` means nothing strong matched: stop searching and do the task directly."""


def apply(text: str, remove: bool = False) -> str:
    """The new file text. Raises ValueError rather than guess at broken markers."""
    begins, ends = text.count(BEGIN), text.count(END)
    if (begins, ends) not in ((0, 0), (1, 1)) or (begins and text.index(BEGIN) > text.index(END)):
        lines = [n for n, line in enumerate(text.splitlines(), 1) if BEGIN in line or END in line]
        raise ValueError(f"broken canopy markers on lines {lines}")
    block = f"{BEGIN}\n{SNIPPET}\n{END}"
    if begins:
        head, rest = text.split(BEGIN)
        tail = rest.split(END)[1]
        return head.rstrip() + "\n" + tail.lstrip("\n") if remove else head + block + tail
    if remove:
        return text
    return (text.rstrip() + "\n\n" if text.strip() else "") + block + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", type=Path, default=Path("~/.claude/CLAUDE.md"))
    ap.add_argument("--show", action="store_true", help="print the snippet and stop")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remove", action="store_true")
    args = ap.parse_args()
    if args.show:
        print(SNIPPET)
        return 0
    target = args.target.expanduser()
    text = target.read_text() if target.exists() else ""
    try:
        new = apply(text, args.remove)
    except ValueError as e:
        print(f"refused: {e}. {target} was not changed.")
        return 1
    if new == text:
        print(f"unchanged: {target}")
        return 0
    if args.dry_run:
        print(f"would {'remove the block from' if args.remove else 'write the block to'} {target}")
        return 0
    if target.exists():
        target.with_name(target.name + ".bak-canopy").write_text(text)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new)
    print(f"{'removed from' if args.remove else 'written to'} {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
