"""Postgres: the connection pool and the numbered migrations.

A migration is `migrations/NNN_name.sql`, runs once, in its own transaction, and is recorded in
`system.schema_version`. An advisory lock means two services starting together take turns.
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

MIGRATIONS = Path(__file__).parent / "migrations"
DATABASE_URL = os.environ.get("CANOPY_DATABASE_URL", "postgres://canopy:canopy@db:5432/canopy")
_LOCK = 7_260_001   # any number, as long as every service uses the same one
_FILE = re.compile(r"(\d{3})_[\w-]+\.sql")


def available(folder: Path = MIGRATIONS) -> list[tuple[int, Path]]:
    found = [(int(m[1]), p) for p in folder.glob("*.sql") if (m := _FILE.fullmatch(p.name))]
    numbers = sorted(n for n, _ in found)
    if numbers != list(range(1, len(numbers) + 1)):
        raise RuntimeError(f"migrations must be numbered 1 upwards with no gaps: {numbers}")
    return sorted(found)


def migrate(url: str = DATABASE_URL, folder: Path = MIGRATIONS) -> list[int]:
    """Apply what has not run yet, and return the numbers applied."""
    applied = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK,))
        conn.execute("CREATE SCHEMA IF NOT EXISTS system")
        conn.execute("CREATE TABLE IF NOT EXISTS system.schema_version ("
                     "version INT PRIMARY KEY, name TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT version FROM system.schema_version")}
        for number, path in available(folder):
            if number in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO system.schema_version (version, name) VALUES (%s, %s)",
                             (number, path.stem))
            applied.append(number)
    return applied


class Row(dict):
    """A row read by column name or by position."""

    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def _rows(cursor):
    names = [c.name for c in cursor.description or []]
    return lambda values: Row(zip(names, values))


class Result:
    def __init__(self, rows: list[Row]):
        self.rows = rows

    def fetchone(self) -> Row | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[Row]:
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class Db:
    """One handle shared by threads. `execute` borrows a connection for the statement, and
    `with db:` holds one for a transaction, on this thread only, so a long sync does not hold up
    search. Table names resolve through `catalogue`, then `system`."""

    def __init__(self, url: str = DATABASE_URL, size: int = 10):
        self.pool = ConnectionPool(
            url, min_size=1, max_size=size, open=True, configure=register_vector,
            kwargs={"autocommit": True, "row_factory": _rows,
                    "options": "-c search_path=catalogue,system,public"})
        self._local = threading.local()

    def __enter__(self) -> "Db":
        depth = getattr(self._local, "depth", 0)
        if depth == 0:
            self._local.conn = self.pool.getconn()
            self._local.tx = self._local.conn.transaction()
            self._local.tx.__enter__()
        self._local.depth = depth + 1
        return self

    def __exit__(self, *exc) -> None:
        self._local.depth -= 1
        if self._local.depth == 0:
            conn, self._local.conn = self._local.conn, None
            try:
                self._local.tx.__exit__(*exc)
            finally:
                self.pool.putconn(conn)

    @staticmethod
    def _run(conn, sql: str, args) -> Result:
        cur = conn.execute(sql, args)
        return Result(cur.fetchall() if cur.description else [])

    def execute(self, sql: str, args=None) -> Result:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return self._run(conn, sql, args)
        with self.pool.connection() as conn:
            return self._run(conn, sql, args)
