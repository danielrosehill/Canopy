#!/bin/sh
# Runs once, on an empty data folder: one database and one role for each owner, so the gateway
# cannot read Canopy's tables, or Canopy the gateway's. Schemas come from numbered migrations.
set -eu
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
  -v gateway_password="$GATEWAY_DB_PASSWORD" -v canopy_password="$CANOPY_DB_PASSWORD" <<'SQL'
CREATE ROLE mcpjungle LOGIN PASSWORD :'gateway_password';
CREATE ROLE canopy LOGIN PASSWORD :'canopy_password';
CREATE DATABASE mcpjungle OWNER mcpjungle;
CREATE DATABASE canopy OWNER canopy;
REVOKE CONNECT ON DATABASE mcpjungle FROM PUBLIC;
REVOKE CONNECT ON DATABASE canopy FROM PUBLIC;
\connect canopy
CREATE EXTENSION vector;
SQL
