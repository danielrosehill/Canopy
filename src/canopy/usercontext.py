"""The user's context: habits, SOPs, preferences and background notes, in a git repo on a volume.

Canopy ships the place, not the content. The repo lives at `$CANOPY_DATA/context` (mounted at /context: read-write
in `api`, read-only in `catalogue`). It is state, not config, so it is writable with or without an overlay. The catalogue indexes it as the context source `user` (CONTEXT_NAME), so the
search an agent runs before a task returns the rules that apply. Agents on any device write to it through the
API, or the catalogue's `context_write` tool, and never need git or a copy of their own.

Every write is a commit, so there is history and undo. With `CONTEXT_REMOTE` set (`owner/repo` or an https URL),
an empty volume starts as a clone of it, and the `context-push` job pulls with rebase and pushes: that is the
copy in the cloud. Without it the repo is local, and the `backup-config` job still covers it. The API is the
only writer. A push that cannot rebase stops and fails the job, which `/v1/status` reports; it never forces.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
from pathlib import Path

from . import index as ix

ROOT = Path(os.environ.get("CANOPY_CONTEXT_DIR", "/context"))
REMOTE = os.environ.get("CONTEXT_REMOTE", "").strip()
REMOTE_REF = os.environ.get("CONTEXT_REMOTE_REF", "").strip() or None
DIRS = {kind: d for d, kind in ix.CONTEXT_DIRS.items()}   # kind -> folder
MAX_BYTES = 200_000
_AUTHOR = ["-c", "core.symlinks=false", "-c", "user.name=Canopy", "-c", "user.email=canopy@localhost", "-c", "commit.gpgsign=false"]

README = """# Context

Your standing instructions for every agent that uses this Canopy. One markdown file per record:

| Folder | Kind | For |
| --- | --- | --- |
| `habits/` | habit | How you want agents to work, whatever the task |
| `sops/` | sop | Procedures for a recurring job |
| `preferences/` | preference | A rule that applies on a trigger, including guardrails ("never X when Y") |
| `context/` | context | Background: your network, accounts, services. Read before asking you |

Frontmatter: `id`, `description` (the trigger and the rule: it is what search matches) and `status`
(`active`, or `archived` to hide it). Agents write here through Canopy (`context_write`), and every write is
a commit. Rules that hold in every session whatever the task stay in your CLAUDE.md: there is nothing to search on.
"""


class ContextError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _name(name: str) -> str:
    name = (name or "").strip().removesuffix(".md")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,80}", name):
        raise ContextError(400, "name: lowercase letters, digits, '.', '_' and '-', up to 81 characters")
    return name


def _kind(kind: str) -> str:
    if kind not in DIRS:
        raise ContextError(400, f"kind must be one of {sorted(DIRS)}")
    return kind


def _yaml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


class UserContext:
    def __init__(self, root: Path = ROOT, remote: str = REMOTE, ref: str | None = REMOTE_REF,
                 on_change=None):
        self.root, self.remote, self.ref = root, remote, ref
        self.on_change = on_change or (lambda: None)   # asks the catalogue to reindex the source
        self.lock = threading.Lock()

    # --- git ---------------------------------------------------------------------

    def git(self, *args: str, ok: tuple[int, ...] = (0,), timeout: int = 120) -> subprocess.CompletedProcess:
        p = subprocess.run(["git", *_AUTHOR, "-c", f"safe.directory={self.root}", "-C", str(self.root), *args], capture_output=True, text=True,
                           timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if p.returncode not in ok:
            msg = (p.stderr or p.stdout).strip()
            if ix.GIT_TOKEN:
                msg = msg.replace(ix.GIT_TOKEN, "***")
            raise ContextError(502, f"git {args[0]}: {msg[-400:]}")
        return p

    def url(self) -> str:
        try:
            return ix.git_url(self.remote)
        except ValueError as e:
            raise ContextError(400, f"CONTEXT_REMOTE: {e}") from None

    def prepare(self) -> str:
        """Make the volume a repo. Empty and with a remote: clone it. Empty without one: a new repo with the
        folders and a README. A repo already there is left as it is."""
        with self.lock:
            if (self.root / ".git").exists():
                return "ready"
            self.root.mkdir(parents=True, exist_ok=True)
            if self.remote and not any(self.root.iterdir()):
                self.git("init", "-q")
                branch = self.ref or self.remote_default()
                self.git("fetch", "-q", self.url(), branch, timeout=300)
                self.git("checkout", "-q", "-B", branch, "FETCH_HEAD")
                made = "cloned"
            else:
                self.git("init", "-q", "-b", "main")
                made = "created"
            for d in DIRS.values():
                (self.root / d).mkdir(exist_ok=True)
                keep = self.root / d / ".gitkeep"
                if not any((self.root / d).iterdir()):
                    keep.touch()
            if not (self.root / "README.md").exists():
                (self.root / "README.md").write_text(README)
            self.git("add", "-A")
            self.git("commit", "-q", "-m", "Canopy: context folders", ok=(0, 1))
        self.on_change()
        return made

    def remote_default(self) -> str:
        """The remote's default branch: `main` is only a guess, and pushing to it made a second branch on a
        repo whose default was `master` (seen 2026-11-14)."""
        p = subprocess.run(["git", "ls-remote", "--symref", self.url(), "HEAD"], capture_output=True, text=True,
                           timeout=60, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        m = re.search(r"^ref: refs/heads/(\S+)\s+HEAD", p.stdout, re.M)
        return m.group(1) if m else "main"

    def branch(self) -> str:
        """Where pushes go: CONTEXT_REMOTE_REF, else the branch the clone checked out."""
        if self.ref:
            return self.ref
        p = self.git("symbolic-ref", "--short", "HEAD", ok=(0, 128))
        return p.stdout.strip() or "main"

    def head(self) -> str | None:
        p = self.git("rev-parse", "HEAD", ok=(0, 128))
        return p.stdout.strip() if p.returncode == 0 else None

    # --- records -------------------------------------------------------------------

    def path(self, kind: str, name: str) -> Path:
        return self.root / DIRS[_kind(kind)] / f"{_name(name)}.md"

    def listing(self) -> dict:
        recs = [{k: r[k] for k in ("id", "kind", "name", "description", "path")}
                for r in ix.extract_context(ix.USER_CONTEXT_NAME, self.root)]
        return {"root": str(self.root), "remote": self.remote or None, "head": self.head(), "records": recs}

    def read(self, kind: str, name: str) -> dict:
        p = self.path(kind, name)
        if not p.is_file():
            raise ContextError(404, f"no {kind} named {name}")
        return {"kind": kind, "name": name, "path": str(p.relative_to(self.root)), "text": p.read_text()}

    def write(self, kind: str, name: str, text: str, description: str | None = None) -> dict:
        """Create or replace one record. Text without frontmatter needs `description`, which becomes it."""
        p = self.path(kind, name)
        text = text or ""
        if len(text.encode()) > MAX_BYTES:
            raise ContextError(413, f"over {MAX_BYTES} bytes")
        fm, _ = ix.frontmatter(text)
        if not fm:
            if not (description or "").strip():
                raise ContextError(400, "needs `description` (the trigger and the rule), or frontmatter with one")
            text = (f"---\nid: {_name(name)}\ndescription: {_yaml_str(description.strip())}\nstatus: active\n---\n\n"
                    + text.lstrip())
        elif not (fm.get("description") or fm.get("summary") or fm.get("title") or description):
            raise ContextError(400, "the frontmatter needs a `description`: it is what search matches")
        with self.lock:
            existed = p.is_file()
            if existed and p.read_text() == text:
                return {"id": f"{kind}:{ix.USER_CONTEXT_NAME}/{_name(name)}", "changed": False, "head": self.head()}
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
            (p.parent / ".gitkeep").unlink(missing_ok=True)
            self.git("add", "-A", "--", str(p.parent))
            self.git("commit", "-q", "-m", f"{'Update' if existed else 'Add'} {kind} {_name(name)}")
            head = self.head()
        self.on_change()
        return {"id": f"{kind}:{ix.USER_CONTEXT_NAME}/{_name(name)}", "changed": True, "created": not existed,
                "head": head}

    def delete(self, kind: str, name: str) -> dict:
        p = self.path(kind, name)
        with self.lock:
            if not p.is_file():
                raise ContextError(404, f"no {kind} named {name}")
            self.git("rm", "-q", "--", str(p))
            self.git("commit", "-q", "-m", f"Remove {kind} {_name(name)}")
            head = self.head()
        self.on_change()
        return {"deleted": f"{kind}:{ix.USER_CONTEXT_NAME}/{_name(name)}", "head": head}

    # --- the cloud copy ----------------------------------------------------------

    def push(self) -> str:
        """Pull with rebase, then push. Without CONTEXT_REMOTE there is nothing to do."""
        if not self.remote:
            return "no CONTEXT_REMOTE: the context is local, and backup-config covers it"
        with self.lock:
            branch = self.branch()
            before = self.head()
            p = self.git("fetch", "-q", self.url(), branch, ok=(0, 128), timeout=300)
            if p.returncode == 0:
                r = self.git("rebase", "-q", "FETCH_HEAD", ok=(0, 1, 128))
                if r.returncode != 0:
                    self.git("rebase", "--abort", ok=(0, 128))
                    raise ContextError(409, "the remote has changes that do not rebase onto this copy: "
                                            "resolve them in a clone of CONTEXT_REMOTE, then run context-push")
            elif "couldn't find remote ref" not in (p.stderr or "").lower():
                raise ContextError(502, f"git fetch: {(p.stderr or '').strip()[-300:]}")
            self.git("push", "-q", self.url(), f"HEAD:refs/heads/{branch}", timeout=300)
            after = self.head()
        if after != before:
            self.on_change()
        return f"pushed {after} to {self.remote} {branch}"

    def actions(self) -> dict:
        return {"context-push": self.push}
