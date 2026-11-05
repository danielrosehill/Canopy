#!/usr/bin/env bash
# The contract suite: the real gateway image from compose.yaml, on throwaway storage, with a
# fixture upstream. Run from anywhere. Choose what to test with the environment:
#   SERVER_MODE=development tests/contract/run.sh
#   MCPJUNGLE_TAG=0.4.8-stdio tests/contract/run.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

export SERVER_MODE="${SERVER_MODE:-enterprise}"
export CONTRACT_GATEWAY_PORT="${CONTRACT_GATEWAY_PORT:-18080}"
export CONTRACT_UPSTREAM_PORT="${CONTRACT_UPSTREAM_PORT:-18081}"
export CONTRACT_DB_PORT="${CONTRACT_DB_PORT:-18082}"
export CANOPY_DATABASE_URL="postgres://canopy:canopy@127.0.0.1:${CONTRACT_DB_PORT}/canopy"
export GATEWAY_URL="http://127.0.0.1:${CONTRACT_GATEWAY_PORT}"
export CONTRACT_UPSTREAM_URL="http://127.0.0.1:${CONTRACT_UPSTREAM_PORT}"
CANOPY_TOKEN_DIR="$(mktemp -d)"
export CANOPY_TOKEN_DIR

compose=(docker compose -p canopy-contract --project-directory . -f compose.yaml -f tests/contract/compose.yaml)
cleanup() {
  "${compose[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$CANOPY_TOKEN_DIR"
}
trap cleanup EXIT

"${compose[@]}" up -d --wait db gateway upstream
# Set here, not exported: to compose this name means the stack's data folder.
CANOPY_DATA="$CANOPY_TOKEN_DIR/catalogue" CANOPY_CONFIG="$CANOPY_TOKEN_DIR/config" \
  uv run pytest tests/contract -rs "$@"
