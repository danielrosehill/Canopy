-- Three owners of content. See "Database" in docs/design.md.
CREATE SCHEMA IF NOT EXISTS system;   -- Canopy's, shipped and migrated
CREATE SCHEMA catalogue;   -- Canopy's, rebuildable from git and the gateway
CREATE SCHEMA workspace;   -- The user's. A migration adds to it, and never rewrites what is there

CREATE TABLE system.sources (
    name TEXT PRIMARY KEY, repo TEXT NOT NULL, ref TEXT, enabled BOOLEAN NOT NULL DEFAULT TRUE);
CREATE TABLE system.model_map (
    model TEXT NOT NULL, provider TEXT NOT NULL, provider_model TEXT NOT NULL,
    PRIMARY KEY (model, provider));
INSERT INTO system.model_map VALUES ('bge-m3', 'local', 'bge-m3'), ('bge-m3', 'openrouter', 'baai/bge-m3');
CREATE TABLE system.jobs (
    name TEXT PRIMARY KEY, schedule TEXT, enabled BOOLEAN NOT NULL DEFAULT TRUE);
CREATE TABLE system.job_runs (
    id BIGSERIAL PRIMARY KEY, job TEXT NOT NULL, started TIMESTAMPTZ NOT NULL, finished TIMESTAMPTZ,
    ok BOOLEAN, output TEXT);
CREATE TABLE system.search_log (
    id BIGSERIAL PRIMARY KEY, at TIMESTAMPTZ NOT NULL DEFAULT now(), query TEXT NOT NULL,
    results TEXT[] NOT NULL, degraded BOOLEAN NOT NULL DEFAULT FALSE);

CREATE TABLE catalogue.plugins (
    name TEXT PRIMARY KEY, marketplace TEXT, repo TEXT, description TEXT,
    head TEXT, status TEXT, error TEXT, synced_at DOUBLE PRECISION);
-- The text and its vector share a row, so they cannot drift apart. `embed_key` is a hash of what
-- was embedded, and `embed_model` the canonical name in system.model_map, whichever route made it.
CREATE TABLE catalogue.records (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, plugin TEXT, name TEXT, description TEXT,
    path TEXT, body TEXT, hash TEXT, embed_key TEXT, embed_model TEXT, embedding vector(1024));
CREATE INDEX records_plugin ON catalogue.records (plugin);
CREATE TABLE catalogue.mcp_decls (
    plugin TEXT, server TEXT, transport TEXT, config TEXT, PRIMARY KEY (plugin, server));
CREATE TABLE catalogue.mcp_refs (
    record_id TEXT, plugin TEXT, route TEXT, server TEXT, tool TEXT,
    PRIMARY KEY (record_id, route, server, tool));
CREATE TABLE catalogue.gateway_servers (name TEXT PRIMARY KEY, description TEXT, enabled BOOLEAN);
CREATE TABLE catalogue.gateway_tools (name TEXT PRIMARY KEY, description TEXT, schema TEXT, core BOOLEAN);
CREATE TABLE catalogue.meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE catalogue.runs (
    id BIGSERIAL PRIMARY KEY, started DOUBLE PRECISION, finished DOUBLE PRECISION, ok BOOLEAN, summary TEXT);
CREATE TABLE catalogue.reads (id TEXT, ts DOUBLE PRECISION, hit BOOLEAN);
CREATE INDEX reads_id ON catalogue.reads (id, ts);

CREATE TABLE workspace.sources (
    name TEXT PRIMARY KEY, repo TEXT NOT NULL, ref TEXT, enabled BOOLEAN NOT NULL DEFAULT TRUE);
CREATE TABLE workspace.namespaces (name TEXT PRIMARY KEY, description TEXT);
CREATE TABLE workspace.memories (
    id BIGSERIAL PRIMARY KEY, namespace TEXT NOT NULL REFERENCES workspace.namespaces (name),
    content TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), embed_model TEXT, embedding vector(1024));
CREATE INDEX memories_namespace ON workspace.memories (namespace);
