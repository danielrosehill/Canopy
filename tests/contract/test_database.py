"""The database as `db-init.sh` and the migrations leave it, on the real image."""
from __future__ import annotations

import psycopg
import pytest

from canopy import store


@pytest.fixture(scope="module")
def conn():
    store.migrate()
    with psycopg.connect(store.DATABASE_URL, autocommit=True) as c:
        yield c


def test_migrations_run_once(conn):
    assert store.migrate() == []
    rows = conn.execute("SELECT version FROM system.schema_version ORDER BY version").fetchall()
    assert [r[0] for r in rows] == [n for n, _ in store.available()]


def test_the_three_schemas_and_pgvector_exist(conn):
    schemas = {r[0] for r in conn.execute("SELECT schema_name FROM information_schema.schemata")}
    assert {"system", "catalogue", "workspace"} <= schemas
    assert conn.execute("SELECT '[1,0]'::vector <=> '[0,1]'::vector").fetchone()[0] == 1


def test_canopy_cannot_reach_the_gateway_database():
    url = store.DATABASE_URL.rsplit("/", 1)[0] + "/mcpjungle"
    with pytest.raises(psycopg.OperationalError, match="permission denied|not have CONNECT"):
        psycopg.connect(url)


def test_the_gateway_keeps_its_tables_in_its_own_database(conn, g):
    g.servers()   # the gateway has migrated by now
    tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert "mcp_servers" not in tables
