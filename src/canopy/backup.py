"""Backup and restore, run by the scheduler inside `api`. restic does the storing and encrypting.

`agent-data/config/backup.json` names the target, and holds no secrets:

    {"repository": "s3:https://s3.example.com/bucket/canopy", "region": "us-east-1",
     "options": {"s3.bucket-lookup": "path"}, "keep": {"daily": 14, "weekly": 8}}

RESTIC_PASSWORD and, for S3, AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY come from the environment.
Keep a copy of RESTIC_PASSWORD outside the stack: `secrets.env` is inside the backup it unlocks.

With a git overlay, agent-data is read-only: CANOPY_CONFIG_EXPORT moves the export, and
CANOPY_AGENT_STATE_DIR adds the local secrets.env, both in the writable state folder.

Two tiers in one repository, told apart by tag. `config` rebuilds the install, `data` restores it
exactly. Each database is dumped from an exported snapshot, with its row counts read from the same
snapshot, so `restore-test` can demand exact counts from a backup of a stack that was busy.

A restore has two halves, because the database must start with the restored passwords:

    python -m canopy.backup restore-files      with nothing running
    python -m canopy.backup restore-db         with only `db` running; --replace to overwrite
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import psycopg
from psycopg import sql

from . import scheduler as sched
from . import store

BACKUPS = Path(os.environ.get("CANOPY_BACKUP_DIR", "/backups"))
AGENT_DATA = Path(os.environ.get("CANOPY_AGENT_DATA_DIR", "/agent-data"))
# With a git overlay, agent-data is read-only. Its local, writable side holds a secrets.env of its own,
# and the export that would otherwise go in config/. Unset: both stay in agent-data, as before.
AGENT_STATE = os.environ.get("CANOPY_AGENT_STATE_DIR", "")
CONFIG_EXPORT = os.environ.get("CANOPY_CONFIG_EXPORT", "")   # default: config/export.json
STATE = [Path(p) for p in ("/api", "/admin", "/tokens")]
CONTEXT = Path(os.environ.get("CANOPY_CONTEXT_DIR", "/context"))   # the user's context: in both backups
DB_HOST = os.environ.get("BACKUP_DB_HOST", "db")
OWNERS = {"mcpjungle": "mcpjungle", "canopy": "canopy"}   # database: role, as in db-init.sh
TIMEOUT = int(os.environ.get("BACKUP_TIMEOUT_SEC", "14400"))
HOST = "canopy"        # the container's hostname changes, and restic groups snapshots by it
NO_REPOSITORY = 10     # restic's exit code for "there is no repository here"
SCRATCH = "restore_test_"
SKIP_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")


class BackupError(RuntimeError):
    pass


def target(config: Path = sched.CONFIG) -> dict:
    path = config / "backup.json"
    if not path.is_file():
        raise BackupError(f"no backup target: {path} does not exist")
    doc = json.loads(path.read_text())
    if not doc.get("repository"):
        raise BackupError(f"{path} names no `repository`")
    return doc


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files_under(paths: list[Path]) -> list[Path]:
    out = []
    for p in paths:
        out += [p] if p.is_file() else sorted(f for f in p.rglob("*") if f.is_file())
    return out


def secret_names(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines = (line.strip() for line in path.read_text().splitlines())
    return sorted({line.split("=", 1)[0].removeprefix("export ").strip()
                   for line in lines if "=" in line and not line.startswith("#")})


class Backups:
    def __init__(self, db: store.Db | None = None, config: Path = sched.CONFIG, backups: Path = BACKUPS,
                 agent_data: Path = AGENT_DATA, state: list[Path] = STATE,
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 agent_state: Path | None = Path(AGENT_STATE) if AGENT_STATE else None,
                 export_path: Path | None = Path(CONFIG_EXPORT) if CONFIG_EXPORT else None):
        self.db, self.config, self.backups, self.agent_data = db, config, backups, agent_data
        self.agent_state, self.export_path = agent_state, export_path or config / "export.json"
        self.state, self._run = state, run
        self.lock = threading.Lock()   # restic's prune wants the repository to itself

    @property
    def secrets_files(self) -> list[Path]:
        """Where a secrets.env can be: agent-data, and the local state beside a read-only overlay."""
        found = [self.agent_data / "secrets.env"]
        if self.agent_state and self.agent_state != self.agent_data:
            found.append(self.agent_state / "secrets.env")
        return found

    # --- the tools -------------------------------------------------------------------

    def command(self, cmd: list[str], env: dict[str, str] | None = None, ok: tuple[int, ...] = (0,)) -> \
            subprocess.CompletedProcess:
        done = self._run(cmd, env={**os.environ, **(env or {})}, capture_output=True, text=True, timeout=TIMEOUT)
        if done.returncode not in ok:
            raise BackupError(f"{' '.join(cmd[:2])} failed ({done.returncode}): {done.stderr.strip()[-1500:]}")
        return done

    def restic(self, *args: str, ok: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
        t = target(self.config)
        if not os.environ.get("RESTIC_PASSWORD"):
            raise BackupError("RESTIC_PASSWORD is not set, so nothing can be encrypted")
        env = {"RESTIC_REPOSITORY": t["repository"], "RESTIC_CACHE_DIR": str(self.backups / "cache")}
        if t.get("region"):
            env["AWS_DEFAULT_REGION"] = t["region"]
        options = [a for k, v in t.get("options", {}).items() for a in ("-o", f"{k}={v}")]
        return self.command(["restic", *args, "--retry-lock", "5m", *options], env, ok)

    def ensure_repository(self) -> None:
        if self.restic("cat", "config", ok=(0, NO_REPOSITORY)).returncode == NO_REPOSITORY:
            self.restic("init")

    def connect(self, name: str) -> psycopg.Connection:
        return psycopg.connect(host=DB_HOST, user="postgres", dbname=name, password=self.password())

    def password(self) -> str:
        return os.environ.get("POSTGRES_PASSWORD", "canopy")

    def pg(self, *cmd: str) -> subprocess.CompletedProcess:
        return self.command([*cmd, "-h", DB_HOST, "-U", "postgres"], {"PGPASSWORD": self.password()})

    @staticmethod
    def tables(conn: psycopg.Connection) -> list[tuple[str, str]]:
        return [tuple(r) for r in conn.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' AND table_schema <> ALL(%s) ORDER BY 1, 2", (list(SKIP_SCHEMAS),))]

    @classmethod
    def counts(cls, conn: psycopg.Connection) -> dict[str, int]:
        return {f"{s}.{t}": conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(s, t))).fetchone()[0]
                for s, t in cls.tables(conn)}

    def dump(self, name: str, path: Path) -> dict[str, int]:
        with self.connect(name) as conn:
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
            counts = self.counts(conn)
            self.pg("pg_dump", "-d", name, "-Fc", f"--snapshot={snapshot}", "-f", str(path))
            conn.rollback()
        self.pg("pg_restore", "--list", str(path))   # refuse an unreadable dump
        return counts

    # --- the jobs --------------------------------------------------------------------

    @property
    def stage(self) -> Path:
        return self.backups / "stage"

    def backup_data(self) -> str:
        with self.lock:
            self.ensure_repository()
            shutil.rmtree(self.stage, ignore_errors=True)
            self.stage.mkdir(mode=0o700, parents=True)
            try:
                state = [p for p in [*self.state, CONTEXT, *self.secrets_files] if p.exists()]
                manifest = {"databases": {n: self.dump(n, self.stage / f"{n}.dump") for n in OWNERS},
                            "files": {str(f): sha256(f) for f in files_under(state)}}
                (self.stage / "manifest.json").write_text(json.dumps(manifest, indent=1))
                self.restic("backup", "--tag", "data", "--host", HOST, str(self.stage), *map(str, state))
            finally:
                shutil.rmtree(self.stage, ignore_errors=True)
            self.forget()
        tables = sum(len(c) for c in manifest["databases"].values())
        return f"{tables} tables, {len(manifest['files'])} files"

    def forget(self) -> None:
        keep = {"daily": 14, "weekly": 8, **target(self.config).get("keep", {})}
        flags = [a for k, v in keep.items() for a in (f"--keep-{k}", str(v))]
        for tag in ("data", "config"):
            self.restic("forget", "--tag", tag, "--group-by", "tags", *flags)
        self.restic("prune")

    def export(self) -> None:
        """What lives only in the database, plus the names of the secrets but not their values."""
        doc = {"sources": [dict(r) for r in self.db.execute("SELECT * FROM workspace.sources ORDER BY name")],
               "namespaces": [dict(r) for r in self.db.execute("SELECT * FROM workspace.namespaces ORDER BY name")],
               "secrets": sorted({n for f in self.secrets_files for n in secret_names(f)})}
        path = self.export_path
        text = json.dumps(doc, indent=1, sort_keys=True, default=str) + "\n"
        if not path.is_file() or path.read_text() != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def backup_config(self) -> str:
        """Often and cheap: restic writes a snapshot only when something changed."""
        with self.lock:
            self.ensure_repository()
            self.export()
            names = ("config", "skills", "notes", "compose.override.yaml")
            paths = [self.agent_data / n for n in names if (self.agent_data / n).exists()]
            if CONTEXT.is_dir():
                paths.append(CONTEXT)
            if self.export_path.exists() and not any(self.export_path.is_relative_to(p) for p in paths):
                paths.append(self.export_path)
            if not paths:
                raise BackupError(f"nothing to back up in {self.agent_data}")
            done = self.restic("backup", "--tag", "config", "--host", HOST, "--skip-if-unchanged",
                               *map(str, paths))
        return done.stdout.strip()[-400:]

    # --- what the API shows ----------------------------------------------------------

    def describe(self) -> dict:
        """The target, which holds no secrets. Raises if there is none."""
        t = target(self.config)
        return {"repository": t["repository"], "region": t.get("region"),
                "keep": {"daily": 14, "weekly": 8, **t.get("keep", {})},
                "password_set": bool(os.environ.get("RESTIC_PASSWORD"))}

    def snapshots(self) -> list[dict]:
        """Newest first. A target nothing has been written to yet has no repository, and no snapshots."""
        done = self.restic("snapshots", "--json", ok=(0, NO_REPOSITORY))
        if done.returncode == NO_REPOSITORY:
            return []
        found = [{"id": s.get("short_id"), "time": s["time"], "tags": s.get("tags", []), "paths": s.get("paths", []),
                  "bytes": (s.get("summary") or {}).get("total_bytes_processed")}
                 for s in json.loads(done.stdout or "[]")]
        return sorted(found, key=lambda s: s["time"], reverse=True)

    def plan(self) -> dict:
        """What `canopy restore` would bring back, and what it would overwrite. It changes nothing:
        a restore stops the stack, so the API cannot run one."""
        found = self.snapshots()
        data = next((s for s in found if "data" in s["tags"]), None)
        if data is None:
            raise BackupError("no `data` snapshot to restore")
        return {"data": data, "config": next((s for s in found if "config" in s["tags"]), None),
                "replaces": [*(f"database {n}" for n in OWNERS), *map(str, self.state),
                             *map(str, self.secrets_files), str(self.config)],
                "rebuilds": ["catalogue clones", "Ollama models"],
                "command": "canopy restore --replace", "stops_the_stack": True}

    def snapshot_exists(self, tag: str) -> bool:
        return bool(json.loads(self.restic("snapshots", "--tag", tag, "--latest", "1", "--json").stdout or "[]"))

    def restore_test(self) -> str:
        """The latest backup, restored beside the live stack and checked. It touches nothing live."""
        with self.lock:
            self.restic("check")
            if not self.snapshot_exists("data"):
                raise BackupError("no `data` snapshot to test")
            where = self.backups / "restore-test"
            shutil.rmtree(where, ignore_errors=True)
            where.mkdir(mode=0o700, parents=True)
            try:
                self.restic("restore", "latest", "--tag", "data", "--target", str(where), "--verify")
                stage = where / self.stage.relative_to("/")
                manifest = json.loads((stage / "manifest.json").read_text())
                for name, want in manifest.get("files", {}).items():
                    copy = where / name.lstrip("/")
                    if not copy.is_file() or sha256(copy) != want:
                        raise BackupError(f"{name} did not come back as it went in")
                rows = sum(self.check_database(n, stage / f"{n}.dump", c) for n, c in manifest["databases"].items())
                config = self.snapshot_exists("config")
                if config:
                    self.restic("restore", "latest", "--tag", "config", "--target", str(where / "config"), "--verify")
            finally:
                shutil.rmtree(where, ignore_errors=True)
        return (f"restored {rows} rows across {len(manifest['databases'])} databases and "
                f"{len(manifest['files'])} files, exact. Config: {'restored' if config else 'no snapshot yet'}")

    def check_database(self, name: str, dump: Path, want: dict[str, int]) -> int:
        scratch = SCRATCH + name
        with psycopg.connect(host=DB_HOST, user="postgres", dbname="postgres", password=self.password(),
                             autocommit=True) as admin:
            drop = sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(scratch))
            admin.execute(drop)
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(scratch)))
            try:
                self.pg("pg_restore", "-d", scratch, "--no-owner", "--no-privileges", "--exit-on-error", str(dump))
                with self.connect(scratch) as conn:
                    got = self.counts(conn)
            finally:
                admin.execute(drop)
        if got != want:
            wrong = {k: (want.get(k), got.get(k)) for k in {*want, *got} if want.get(k) != got.get(k)}
            raise BackupError(f"{name} came back different, as (wanted, got): {wrong}")
        return sum(got.values())

    # --- a real restore --------------------------------------------------------------

    def restore_files(self) -> str:
        """Everything but the databases, back where it came from. The dumps wait in the stage folder."""
        if not self.snapshot_exists("data"):
            raise BackupError("no `data` snapshot to restore")
        self.restic("restore", "latest", "--tag", "data", "--target", "/", "--verify")
        config = self.snapshot_exists("config")
        if config:
            self.restic("restore", "latest", "--tag", "config", "--target", "/", "--verify")
        return f"files restored. Config: {'restored' if config else 'no snapshot'}"

    def restore_db(self, replace: bool = False) -> str:
        manifest = json.loads((self.stage / "manifest.json").read_text())
        for name in manifest["databases"]:
            with self.connect(name) as conn:
                if not replace and any(self.counts(conn).values()):
                    raise BackupError(f"{name} already holds data. Use --replace to overwrite it")
        with psycopg.connect(host=DB_HOST, user="postgres", dbname="postgres", password=self.password(),
                             autocommit=True) as admin:
            for name, role in OWNERS.items():
                ident = sql.Identifier(name)
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(ident))
                admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(ident, sql.Identifier(role)))
                admin.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(ident))
        for name, want in manifest["databases"].items():
            self.pg("pg_restore", "-d", name, "--exit-on-error", str(self.stage / f"{name}.dump"))
            with self.connect(name) as conn:
                if self.counts(conn) != want:
                    raise BackupError(f"{name} did not restore to the row counts in the manifest")
        shutil.rmtree(self.stage, ignore_errors=True)
        return "databases restored, row counts exact"

    def actions(self) -> dict[str, Callable[[], str]]:
        return {"backup-data": self.backup_data, "backup-config": self.backup_config,
                "restore-test": self.restore_test}


def main(argv: list[str]) -> int:
    b = Backups()
    if argv[:1] == ["restore-files"]:
        print(b.restore_files())
    elif argv[:1] == ["restore-db"]:
        print(b.restore_db("--replace" in argv))
    else:
        sys.exit(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
