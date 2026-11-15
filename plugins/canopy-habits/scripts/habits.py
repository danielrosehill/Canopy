#!/usr/bin/env python3
"""canopy-habits: render and check habits and SOPs that live in Canopy.

Canopy is the store. This script never talks to it: the skills fetch records with the
catalogue tools and hand them over as JSON (a file, or `-` for stdin), so it needs no
network and no credentials. Standard library only.

Records JSON, either of these shapes:

    {"habits": [{"name": "agent-first", "text": "<full file text>"}, ...],
     "sops":   [{"name": "publish-a-package", "text": "..."}, ...]}
    [{"kind": "habit", "name": "...", "text": "..."}, ...]

    (a habit record may be only its frontmatter: sync and check read nothing below it)
    habits.py sync RECORDS [--target F ...] [--write]   render the block; diff or write it
    habits.py check RECORDS [--target F ...] [--json]   validate records, budget, staleness
    habits.py validate FILE --kind habit|sop [--name N] validate one record's text
    habits.py where                                     candidate instruction files + block state
    habits.py library [--json] [--repo R | --dir D]     list a habit library (default danielrosehill/Agent-Habits)
    habits.py adopt ID [--set N=V ...] [--repo R]       print one library habit rendered for the user
    habits.py adopt --all [--set N=V ...] [--skip a,b]  every library habit, rendered, as records JSON
    habits.py index-library --dir CLONE                 rebuild index.json in a clone of a library repo

A library is a git repo with library/habits/*.md (and index.json). It is read-only here:
fetched with a shallow clone into a temp dir. Libraries come from --repo, else
$CANOPY_HABITS_LIBRARIES (comma-separated owner/repo[@ref]), else the default below.

The block sits between the markers below in the user-level instruction file. `sync`
without --target prints the block only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import re
import shutil
import sys
from pathlib import Path

import os
import subprocess
import tempfile

DEFAULT_LIBRARY = "danielrosehill/Agent-Habits"
LIBRARY_PATH = "library/habits"
LIBRARY: Path = Path("/nonexistent")   # set by resolve_library

BEGIN = "<!-- agent-habits:begin -->"
END = "<!-- agent-habits:end -->"
# Markers from earlier versions; sync replaces them in place.
LEGACY = [("<!-- habits-of-claude:begin -->", "<!-- habits-of-claude:end -->"),
          ("<!-- claude-sops:begin -->", "<!-- claude-sops:end -->")]
HOME = Path.home()
CANDIDATES = [HOME / "CLAUDE.md", HOME / ".claude" / "CLAUDE.md", HOME / "AGENTS.md",
              HOME / ".codex" / "AGENTS.md", HOME / ".gemini" / "GEMINI.md"]
STANCES = ("practice", "prohibition")
STATUSES = ("active", "retired")
AUTONOMY = ("auto", "confirm", "confirm-each", "manual")
SUMMARY_MAX = 320   # characters; the summary sits in every session's context
BLOCK_BUDGET = 7000  # characters for the whole block, roughly 1,750 tokens
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PLACEHOLDER = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
RESERVED = {"README", "INDEX"}


def today() -> str:
    return dt.date.today().isoformat()


# ---------------------------------------------------------------- parsing

def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def split_front(text: str, label: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise ValueError(f"{label}: missing frontmatter")
    try:
        _, fm, body = text.split("---\n", 2)
    except ValueError:
        raise ValueError(f"{label}: frontmatter is not closed") from None
    return fm, body


def parse_fm(fm: str, label: str) -> dict:
    """Scalars, inline lists, `- ` block lists and a one-level `variables:` map."""
    meta, block, key = {"variables": {}}, None, None
    for line in fm.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t" and block == "variables":
            name, _, value = line.strip().partition(":")
            meta["variables"][name.strip()] = unquote(re.sub(r"\s+#.*$", "", value.strip()))
            continue
        if line.lstrip().startswith("- ") and key is not None and isinstance(meta.get(key), list):
            meta[key].append(unquote(line.lstrip()[2:]))
            continue
        if line[0] in " \t":
            raise ValueError(f"{label}: indented line outside a variables block")
        block = None
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [unquote(v) for v in value[1:-1].split(",") if v.strip()]
        elif key == "order":
            meta[key] = int(value)
        elif key == "variables" and not value:
            block = "variables"
        elif value == "" and key in ("tags", "requires", "supersedes", "related"):
            meta[key] = []
        else:
            meta[key] = unquote(value)
    return meta


def parse_habit(text: str, name: str, strict: bool = True) -> dict:
    label = f"habit {name}"
    fm, body = split_front(text, label)
    meta = parse_fm(fm, label)
    for key in ("id", "title", "stance", "order"):
        if key not in meta:
            raise ValueError(f"{label}: frontmatter missing '{key}'")
    meta.setdefault("kind", "habit")
    meta.setdefault("status", "active")
    if meta["id"] != name:
        raise ValueError(f"{label}: id '{meta['id']}' does not match the record name")
    if not ID_RE.match(name):
        raise ValueError(f"{label}: name must be lowercase-with-hyphens")
    if meta["kind"] != "habit":
        raise ValueError(f"{label}: kind must be habit (procedures are SOPs)")
    if meta["stance"] not in STANCES:
        raise ValueError(f"{label}: stance must be one of {STANCES}")
    if meta["status"] not in STATUSES:
        raise ValueError(f"{label}: status must be one of {STATUSES}")
    if meta["status"] == "active" and not meta.get("summary"):
        raise ValueError(f"{label}: active habit needs a 'summary'")
    if len(meta.get("summary", "")) > SUMMARY_MAX:
        raise ValueError(f"{label}: summary is {len(meta['summary'])} chars, max {SUMMARY_MAX}")
    sections = {m.group(1).strip().lower(): m.group(2).strip()
                for m in re.finditer(r"^## (.+?)\n(.*?)(?=^## |\Z)", body, re.S | re.M)}
    if "snippet" not in sections and strict:
        raise ValueError(f"{label}: no '## Snippet' section")
    meta["template"] = sections.get("snippet", "")
    used = set(PLACEHOLDER.findall(meta["template"])) | set(PLACEHOLDER.findall(meta.get("summary", "")))
    declared = set(meta["variables"])
    if used - declared:
        raise ValueError(f"{label}: undeclared variable(s): {', '.join(sorted(used - declared))}")
    if declared - used and strict:
        raise ValueError(f"{label}: unused variable(s): {', '.join(sorted(declared - used))}")
    return meta


def parse_sop(text: str, name: str) -> dict:
    label = f"sop {name}"
    fm, body = split_front(text, label)
    meta = parse_fm(fm, label)
    meta.setdefault("id", name)
    problems = []
    for key in ("id", "title", "trigger", "last_verified"):
        if not meta.get(key):
            problems.append(f"missing required field `{key}`")
    if meta["id"] != name:
        problems.append(f"id {meta['id']!r} does not match the record name")
    if not ID_RE.match(name) or name.upper() in RESERVED:
        problems.append("name must be lowercase-with-hyphens and not reserved")
    lv = str(meta.get("last_verified", ""))
    if lv and not DATE_RE.match(lv):
        problems.append(f"last_verified {lv!r} is not YYYY-MM-DD")
    if meta.get("autonomy", "confirm") not in AUTONOMY:
        problems.append(f"autonomy must be one of {AUTONOMY}")
    if "## Steps" not in body:
        problems.append("no `## Steps` section")
    if "PLACEHOLDER" in text:
        problems.append("template placeholder left in place")
    if problems:
        raise ValueError(f"{label}: " + "; ".join(problems))
    return meta


def render_vars(meta: dict, overrides: dict) -> dict:
    values = {k: overrides.get(k, v) for k, v in meta["variables"].items()}
    sub = lambda t: PLACEHOLDER.sub(lambda m: values[m.group(1)], t)
    meta["summary_rendered"] = sub(meta.get("summary", ""))
    return values


# ---------------------------------------------------------------- records

def load_records(arg: str) -> tuple[list[dict], list[dict], list[str]]:
    """(habits, sops, problems). Habits: active only, sorted by order. SOPs: active, by id."""
    raw = json.loads(sys.stdin.read() if arg == "-" else Path(arg).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        raw = {"habits": [r for r in raw if r.get("kind") == "habit"],
               "sops": [r for r in raw if r.get("kind") == "sop"]}
    habits, sops, problems = [], [], []
    for r in raw.get("habits", []):
        try:
            h = parse_habit(r["text"], r["name"], strict=False)
            render_vars(h, {})
            habits.append(h)
        except (ValueError, KeyError) as e:
            problems.append(str(e))
    for r in raw.get("sops", []):
        try:
            sops.append(parse_sop(r["text"], r["name"]))
        except (ValueError, KeyError) as e:
            problems.append(str(e))
    titles = [h["title"].lower() for h in habits]
    for h in habits:
        if titles.count(h["title"].lower()) > 1:
            problems.append(f"habit {h['id']}: duplicate title '{h['title']}'")
    habits.sort(key=lambda h: h["order"])
    sops.sort(key=lambda s: s["id"])
    return habits, sops, problems


def active(items: list[dict]) -> list[dict]:
    return [i for i in items if i.get("status", "active") == "active"]


# ---------------------------------------------------------------- the block

def build_block(habits: list[dict], sops: list[dict], folders: str = "") -> str:
    """`folders` (a directory holding habits/ and sops/) renders the file-pointer wording of
    the local-folder plugin; it exists to compare output against an older block."""
    habits, sops = active(habits), active(sops)
    always = [h for h in habits if not h.get("trigger")]
    triggered = [h for h in habits if h.get("trigger")]
    line = lambda h: f"- **{h['title']}** (`{h['id']}`): {h['summary_rendered']}"
    if folders:
        hdir, sdir = f"{folders}/habits", f"{folders}/sops"
        head = [f"<!-- Generated by Agent-Habits on {today()} from {folders}. Edit the files"
                " there, then run `habits.py install --write`. -->", "",
                "## Habits", "",
                "Standing habits, in force before the task is known. Each line is the rule. The",
                f"full text, with exceptions and rationale, is `{hdir}/<id>.md`. Read it when a",
                "task falls squarely under a habit, or when the line leaves you unsure.", ""]
        proc = ["## Procedures", "",
                f"Recurring procedures (SOPs) live in `{sdir}/`. Before improvising a multi-step",
                "job, check whether one matches below; if one does, read that `<id>.md` and",
                "follow it, at its stated autonomy. If the same sequence comes up a second time",
                "and no SOP covers it, offer to write one.", ""]
    else:
        head = [f"<!-- Generated by canopy-habits on {today()} from your Canopy. Change the records"
                " (skill capture-habit), then run sync-habits. -->", "",
                "## Habits", "",
                "Standing habits, in force before the task is known. Each line is the rule. The",
                "full text, with exceptions and rationale, is the Canopy record `habit:user/<id>`",
                "(`catalogue__read`). Read it when a task falls squarely under a habit, or when",
                "the line leaves you unsure.", ""]
        proc = ["## Procedures", "",
                "Recurring procedures (SOPs) are Canopy records `sop:user/<id>`. Before improvising",
                "a multi-step job, check whether one matches below; if one does, `catalogue__read`",
                "it and follow it, at its stated autonomy. If the same sequence comes up a second",
                "time and no SOP covers it, offer to write one.", ""]
    out = [BEGIN, *head, *map(line, always)]
    if triggered:
        out += ["", "These apply only when their trigger does:", ""]
        out += [f"- *When {h['trigger']}:* **{h['title']}** (`{h['id']}`): {h['summary_rendered']}"
                for h in triggered]
    out += ["", *proc]
    out += ([f"- When {s.get('trigger', '')}: `{s['id']}`" for s in sops] or ["None written yet."])
    out += ["", END]
    return "\n".join(out)


def splice(text: str, block: str):
    pairs = [(b, e) for b, e in [(BEGIN, END)] + LEGACY if b in text or e in text]
    for b, e in pairs:
        if text.count(b) != 1 or text.count(e) != 1:
            sys.exit(f"marker {b} or {e} appears other than exactly once; fix by hand")
    if not pairs:
        return text.rstrip("\n") + "\n\n" + block + "\n", "appended new block"
    (b0, e0), rest = pairs[0], pairs[1:]
    head, _, tail = text.partition(b0)
    text = head + block + tail.partition(e0)[2]
    for b, e in rest:
        head, _, tail = text.partition(b)
        text = head.rstrip("\n") + "\n" + tail.partition(e)[2]
    return text, "updated existing block" + (f", removed {len(rest)} legacy block(s)" if rest else "")


def same_ignoring_dates(a: str, b: str) -> bool:
    strip = lambda s: re.sub(r"\d{4}-\d{2}-\d{2}", "", s)
    return strip(a) == strip(b)


def current_block(text: str) -> str:
    return BEGIN + text.partition(BEGIN)[2].partition(END)[0] + END if BEGIN in text else ""


# ---------------------------------------------------------------- commands

def cmd_sync(a):
    habits, sops, problems = load_records(a.records)
    if problems:
        sys.exit("not syncing, records are invalid:\n  " + "\n  ".join(problems))
    block = build_block(habits, sops, a.folders)
    if len(block) > BLOCK_BUDGET and not a.force:
        sys.exit(f"block is {len(block)} chars, budget {BLOCK_BUDGET}: give habits a trigger, "
                 "merge or retire some (or --force)")
    if not a.target:
        print(block)
        print(f"\n-- {len(block)} chars of {BLOCK_BUDGET}; {len(active(habits))} habits, "
              f"{len(active(sops))} SOPs", file=sys.stderr)
        return
    for t in a.target:
        target = Path(t).expanduser()
        original = target.read_text(encoding="utf-8") if target.exists() else ""
        updated, action = splice(original, block)
        if same_ignoring_dates(updated, original):
            print(f"{target}: already up to date")
            continue
        sys.stdout.writelines(difflib.unified_diff(
            original.splitlines(keepends=True), updated.splitlines(keepends=True),
            fromfile=str(target), tofile=f"{target} (proposed)"))
        if not a.write:
            print(f"\n-- dry run: {action}. Re-run with --write.")
            continue
        if original:
            backup = target.with_suffix(target.suffix + f".bak-{dt.datetime.now():%Y%m%d-%H%M%S}")
            shutil.copy2(target, backup)
            print(f"backup: {backup}")
        target.write_text(updated, encoding="utf-8")
        print(f"{target}: {action}")


def cmd_check(a):
    habits, sops, fails = load_records(a.records)
    warns = []
    ids = {h["id"] for h in habits}
    for h in habits:
        for ref in h.get("related", []):
            if ref not in ids:
                warns.append(f"{h['id']}: related habit '{ref}' is not an active habit")
        if h.get("status") == "active" and PLACEHOLDER.search(h["template"] + h.get("summary", "")):
            warns.append(f"{h['id']}: still has {{{{VARIABLES}}}}; adopt renders them")
    act_h, act_s = active(habits), active(sops)
    block = build_block(act_h, act_s)
    if len(block) > BLOCK_BUDGET:
        fails.append(f"block would be {len(block)} chars, budget {BLOCK_BUDGET}")
    for t in a.target or [str(p) for p in CANDIDATES if p.exists() and BEGIN in p.read_text(encoding="utf-8")]:
        target = Path(t).expanduser()
        if not target.exists():
            fails.append(f"{target} does not exist")
            continue
        text = target.read_text(encoding="utf-8")
        if text.count(BEGIN) != 1 or text.count(END) != 1:
            fails.append(f"{target}: expected one {BEGIN} block, found {text.count(BEGIN)}")
            continue
        for b, _ in LEGACY:
            if b in text:
                fails.append(f"{target}: legacy block {b} still present; run sync")
        outside = text.replace(current_block(text), "")
        for heading in ("Habits", "Procedures", "Standard operating procedures"):
            if re.search(rf"^#{{1,3}} {heading}\s*$", outside, re.M):
                fails.append(f"{target}: a hand-written '## {heading}' heading exists outside the block")
        updated, _ = splice(text, block)
        if not same_ignoring_dates(updated, text):
            fails.append(f"{target}: block is stale against the Canopy records; run sync-habits")
    report = {"ok": not fails, "fail": fails, "warn": warns, "habits": len(act_h), "sops": len(act_s),
              "block_chars": len(block)}
    print(json.dumps(report, indent=2) if a.json else
          "\n".join([("ok" if not fails else "FAIL")] + [f"  fail: {f}" for f in fails] +
                    [f"  warn: {w}" for w in warns]))
    sys.exit(0 if not fails else 1)


def cmd_validate(a):
    text = sys.stdin.read() if a.file == "-" else Path(a.file).read_text(encoding="utf-8")
    try:
        meta = (parse_habit if a.kind == "habit" else parse_sop)(text, a.name or Path(a.file).stem)
    except ValueError as e:
        sys.exit(str(e))
    print(f"ok: {a.kind} {meta['id']}")


def cmd_where(a):
    rows = []
    for p in CANDIDATES:
        if p.exists():
            t = p.read_text(encoding="utf-8")
            rows.append({"file": str(p), "has_block": BEGIN in t,
                         "legacy_block": any(b in t for b, _ in LEGACY)})
    print(json.dumps(rows, indent=2))


def libraries() -> list[str]:
    env = os.environ.get("CANOPY_HABITS_LIBRARIES", "").strip()
    return [x.strip() for x in env.split(",") if x.strip()] or [DEFAULT_LIBRARY]


def resolve_library(a) -> Path:
    """The library's habits folder: --dir (a local clone), else a shallow clone of --repo."""
    global LIBRARY
    if getattr(a, "dir", ""):
        LIBRARY = Path(a.dir).expanduser() / LIBRARY_PATH
    else:
        repo, _, ref = (getattr(a, "repo", "") or libraries()[0]).partition("@")
        tmp = tempfile.mkdtemp(prefix="canopy-habits-lib-")
        cmd = ["git", "clone", "--depth", "1", "--quiet"] + (["--branch", ref] if ref else [])
        url = repo if "://" in repo else f"https://github.com/{repo}.git"
        run = subprocess.run(cmd + [url, tmp + "/lib"], capture_output=True, text=True)
        if run.returncode:
            sys.exit(f"could not clone library {repo}: {run.stderr.strip()}")
        LIBRARY = Path(tmp) / "lib" / LIBRARY_PATH
    if not LIBRARY.is_dir():
        sys.exit(f"no {LIBRARY_PATH}/ in that library")
    return LIBRARY


def library() -> list[dict]:
    out = []
    for p in sorted(LIBRARY.glob("*.md")):
        if p.stem in RESERVED:
            continue
        m = parse_habit(p.read_text(encoding="utf-8"), p.stem)
        m["file"] = p.name
        out.append(m)
    return sorted(out, key=lambda m: m["order"])


def cmd_library(a):
    resolve_library(a)
    rows = [{"id": m["id"], "title": m["title"], "order": m["order"], "trigger": m.get("trigger"),
             "variables": m["variables"], "revised": m.get("revised") or m.get("added"),
             "summary": m["summary"]} for m in library()]
    if a.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    for r in rows:
        when = f"  (when {r['trigger']})" if r["trigger"] else ""
        print(f"{r['order']:>3}  {r['id']}{when}\n     {r['summary']}")
    print(f"\n{len(rows)} library habits")


def adopt_text(habit_id: str, overrides: dict) -> str:
    src = LIBRARY / f"{habit_id}.md"
    if not src.exists():
        sys.exit(f"no library habit '{habit_id}'; see `habits.py library`")
    text = src.read_text(encoding="utf-8")
    meta = parse_habit(text, habit_id)
    unknown = set(overrides) - set(meta["variables"])
    if unknown:
        sys.exit(f"'{habit_id}' has no variable(s): {', '.join(sorted(unknown))}")
    values = render_vars(meta, overrides)
    _, fm, body = text.split("---\n", 2)
    kept, skip = [], False
    for line in fm.splitlines():
        if line.startswith("variables:"):
            skip = True
            continue
        if skip and line[:1] in (" ", "\t"):
            continue
        skip = False
        if line.split(":", 1)[0].strip() in ("source", "library", "library_version"):
            continue
        kept.append(line)
    version = meta.get("revised") or meta.get("added", "")
    kept += [f'source: "Habits library, adopted {today()}"', f"library: {habit_id}",
             f"library_version: {version}"]
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], "---\n" + "\n".join(kept) + "\n---\n" + body)


def cmd_adopt(a):
    resolve_library(a)
    overrides = dict(kv.split("=", 1) for kv in a.set)
    if a.all:
        skip = {x for x in a.skip.split(",") if x}
        recs = []
        for m in library():
            if m["id"] in skip:
                continue
            # --set applies to every habit that declares the variable; others keep defaults
            own = {k: v for k, v in overrides.items() if k in m["variables"]}
            text = adopt_text(m["id"], own)
            recs.append({"name": m["id"], "text": text,
                         "description": parse_habit(text, m["id"])["summary"]})
        print(json.dumps({"habits": recs}, indent=2, ensure_ascii=False))
        return
    if not a.id:
        sys.exit("give a habit id, or --all")
    sys.stdout.write(adopt_text(a.id, overrides))


def cmd_index_library(a):
    if not a.dir:
        sys.exit("--dir CLONE is required: index-library rewrites index.json in a clone of the library repo")
    resolve_library(a)
    rows = []
    keys = ("id", "title", "kind", "stance", "order", "trigger", "added", "revised", "status", "source", "related")
    for m in library():
        row = {k: m[k] for k in keys if k in m}
        row["summary"] = m["summary"]
        if m["variables"]:
            row["variables"] = m["variables"]
        row["file"] = m["file"]
        rows.append(row)
    payload = {"generated_by": "canopy-habits scripts/habits.py", "generated": today(),
               "count": len(rows), "habits": rows}
    out = LIBRARY / "index.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sync"); p.set_defaults(func=cmd_sync)
    p.add_argument("records", help="records JSON file, or - for stdin")
    p.add_argument("--target", action="append", default=[])
    p.add_argument("--write", action="store_true")
    p.add_argument("--force", action="store_true", help="ignore the size budget")
    p.add_argument("--folders", default="", help="render local-folder pointers (for comparing old blocks)")
    p = sub.add_parser("check"); p.set_defaults(func=cmd_check)
    p.add_argument("records"); p.add_argument("--target", action="append", default=[])
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("validate"); p.set_defaults(func=cmd_validate)
    p.add_argument("file", help="file with the record text, or - for stdin")
    p.add_argument("--kind", choices=("habit", "sop"), required=True)
    p.add_argument("--name", default="")
    sub.add_parser("where").set_defaults(func=cmd_where)
    lib = lambda p: (p.add_argument("--repo", default=""), p.add_argument("--dir", default=""))
    p = sub.add_parser("library"); p.set_defaults(func=cmd_library); lib(p)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("adopt"); p.set_defaults(func=cmd_adopt); lib(p)
    p.add_argument("id", nargs="?", default=""); p.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    p.add_argument("--all", action="store_true"); p.add_argument("--skip", default="")
    p = sub.add_parser("index-library"); p.set_defaults(func=cmd_index_library); lib(p)
    a = ap.parse_args()
    try:
        a.func(a)
    except ValueError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
