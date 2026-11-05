"""Postgres: the connection pool and the numbered migrations.

A migration is `migrations/NNN_name.sql`, runs once, in its own transaction, and is recorded in
`system.schema_version`. An advisory lock means two services starting together take turns.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
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


def pool(url: str = DATABASE_URL, size: int = 8) -> ConnectionPool:
    """Readers and a long sync each get their own connection, so a sync cannot hold up search."""
    return ConnectionPool(url, min_size=1, max_size=size, open=True, kwargs={"row_factory": dict_row},
                          configure=register_vector)
