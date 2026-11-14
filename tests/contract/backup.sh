#!/usr/bin/env bash
# Proves backup and restore on a clean install, with real restic, Postgres and an S3 target: a
# throwaway Garage. Build, back up, destroy every folder, restore from the target, and check.
# The first install uses its own database passwords, so a restore that gets its order wrong fails.
set -euo pipefail
cd "$(dirname "$0")/../.."

base="${BACKUP_PROOF_DIR:-$HOME/.cache}"   # not /tmp, which can be RAM
mkdir -p "$base"
root="$(mktemp -d "$base/canopy-backup-proof.XXXXXX")"
s3=canopy-proof-s3
export COMPOSE_PROJECT_NAME=canopy-proof CANOPY_TAG=proof
export CANOPY_AGENT_DATA="$root/agent-data" CANOPY_DATA="$root/data"
export CANOPY_PORT=18090 CANOPY_API_PORT=18091 CANOPY_API_URL=http://127.0.0.1:18091
canopy() { clients/canopy "$@"; }
say() { printf '\n== %s\n' "$*"; }
wipe() { docker run --rm -v "$root:/r" alpine:3.22 sh -c "rm -rf /r/$1"; }

cleanup() {
  canopy compose down -v --remove-orphans >/dev/null 2>&1 || true
  docker rm -f "$s3" >/dev/null 2>&1 || true
  docker network rm "$s3" >/dev/null 2>&1 || true
  docker run --rm -v "$root/..:/p" alpine:3.22 rm -rf "/p/$(basename "$root")" || true
}
trap cleanup EXIT

say "S3 target"
docker network create "$s3" >/dev/null
mkdir -p "$root/garage"
cat > "$root/garage/garage.toml" <<TOML
metadata_dir = "/var/lib/garage/meta"
data_dir = "/var/lib/garage/data"
db_engine = "sqlite"
replication_factor = 1
rpc_bind_addr = "[::]:3901"
rpc_secret = "$(openssl rand -hex 32)"
[s3_api]
s3_region = "us-east-1"
api_bind_addr = "[::]:3900"
TOML
docker run -d --name "$s3" --network "$s3" --label com.centurylinklabs.watchtower.enable=false \
  -v "$root/garage/garage.toml:/etc/garage.toml:ro" -v "$root/garage/store:/var/lib/garage" \
  dxflrs/garage:v2.5.0 >/dev/null
garage() { docker exec "$s3" /garage "$@"; }
for _ in $(seq 30); do garage status >/dev/null 2>&1 && break; sleep 1; done
node="$(garage node id -q | cut -d@ -f1)"
garage layout assign -z proof -c 1G "$node" >/dev/null
garage layout apply --version 1 >/dev/null
garage bucket create backups >/dev/null
key="$(garage key create proof)"
garage bucket allow --read --write --owner backups --key proof >/dev/null
key_id="$(awk '/Key ID/ {print $NF}' <<<"$key")"
key_secret="$(awk '/Secret key/ {print $NF}' <<<"$key")"

target() {   # what a user must put in place by hand before a restore
  mkdir -p "$CANOPY_AGENT_DATA/config"
  cat > "$CANOPY_AGENT_DATA/config/backup.json" <<JSON
{"repository": "s3:http://$s3:3900/backups/canopy", "region": "us-east-1",
 "options": {"s3.bucket-lookup": "path"}}
JSON
  cat > "$CANOPY_AGENT_DATA/compose.override.yaml" <<YAML
services:
  api:
    networks: [canopy, s3]
networks:
  s3:
    name: $s3
    external: true
YAML
  cat >> "$CANOPY_AGENT_DATA/secrets.env" <<ENV
RESTIC_PASSWORD=proof-repository-password
AWS_ACCESS_KEY_ID=$key_id
AWS_SECRET_ACCESS_KEY=$key_secret
ENV
}

say "First install"
canopy init >/dev/null
target
cat >> "$CANOPY_AGENT_DATA/secrets.env" <<ENV
POSTGRES_PASSWORD=proof-postgres
GATEWAY_DB_PASSWORD=proof-gateway
CANOPY_DB_PASSWORD=proof-canopy
ENV
canopy compose build api cli >/dev/null
canopy up

psql() { canopy compose exec -T db psql -U postgres -d canopy -Atc "$1"; }
mkdir -p "$CANOPY_AGENT_DATA/skills/proof" "$CANOPY_AGENT_DATA/notes"
echo "proof skill" > "$CANOPY_AGENT_DATA/skills/proof/SKILL.md"
echo "proof note" > "$CANOPY_AGENT_DATA/notes/proof.md"
psql "INSERT INTO workspace.namespaces VALUES ('proof', 'survives a restore')" >/dev/null
api_token="$(cat "$CANOPY_DATA/api/token")"
agent_token="$(canopy token)"
before="$(canopy servers)"

say "Your context: a write is a commit, indexed and found by search"
printf 'Proof repos go in the proof org.\n' > "$root/proof-rule.md"
canopy context put preference proof-rule "$root/proof-rule.md" "Creating a proof repo: use the proof org" \
  | python3 -c "import json, sys; d = json.load(sys.stdin); assert d['created'] and d['head'], d"
canopy context | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert [r['id'] for r in d['records']] == ['preference:user/proof-rule'], d"
found=""
for _ in $(seq 30); do
  if curl -fsS -H "Authorization: Bearer $api_token" -H 'Content-Type: application/json' \
       -d '{"query": "creating a proof repo", "kind": "preference"}' "$CANOPY_API_URL/v1/search" \
       | grep -q 'preference:user/proof-rule'; then found=1; break; fi
  sleep 2
done
[ -n "$found" ] || { echo "the context record was not searchable"; exit 1; }

say "Back up"
job_ok() {   # a job's latest run, newer than run id $2. Runs are started in the background
  for _ in $(seq 150); do
    if canopy jobs runs "$1" | python3 -c "
import json, sys
runs = json.load(sys.stdin)
if not runs or runs[0]['id'] <= $2 or runs[0]['finished'] is None:
    sys.exit(3)
last = runs[0]
print('$1:', last['ok'], round(last['seconds'], 1), 's:', last['output'])
sys.exit(0 if last['ok'] else 1)"; then return 0; else code=$?; fi
    [ "$code" = 3 ] || return 1
    sleep 2
  done
  echo "$1 did not finish"; return 1
}
last_run() { psql "SELECT coalesce(max(id), 0) FROM system.job_runs"; }
mark="$(last_run)"
canopy jobs run backup-config >/dev/null
job_ok backup-config "$mark"
canopy jobs run backup-data >/dev/null
job_ok backup-data "$mark"
job_ok restore-test "$mark"

say "the management routes on the real stack"
canopy validate
canopy diff | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['ok'] and not d['register'] and not d['remove'] and not d['rebuild_groups'], d"
canopy marketplaces
canopy embed
canopy sync
canopy refresh toolbox | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['refreshed'] == 'toolbox' and d['tools'] > 0 and not d['removed'], d"
canopy backups | python3 -c "
import json, sys
assert any('data' in s['tags'] for s in json.load(sys.stdin))"
canopy backup-plan | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['data'] and d['command'] == 'canopy restore --replace', d"
canopy compose exec -T api python - <<'PY'
from canopy import gateway as gw
names = {t["name"] for t in gw.McpClient(f"{gw.GATEWAY_URL}/v0/groups/core/mcp", timeout=20).tools()}
assert len(names) == 12 and not names & set(gw.ADMIN_TOOLS), sorted(names)
assert {"catalogue__context_write", "catalogue__context_delete"} <= names, sorted(names)
PY
for _ in $(seq 60); do canopy status >/dev/null 2>&1 && break; sleep 2; done   # the sync above is settling
canopy status | tee /dev/stderr | python3 -c "
import json, sys
d = json.load(sys.stdin)['components']['jobs']
assert d['ok'] and d['backup']['state'] == 'on' and d['backup']['last_ok_hours'] is not None, d"

say "The test can fail: wrong row counts on a real restore"
canopy compose exec -T api python - <<'PY'
from pathlib import Path
from canopy.backup import Backups, BackupError
b, dump = Backups(), Path("/backups/negative.dump")
b.dump("canopy", dump)
try:
    b.check_database("canopy", dump, {"system.jobs": 999})
except BackupError as e:
    print("refused:", str(e)[:120])
else:
    raise SystemExit("a wrong manifest passed")
finally:
    dump.unlink()
PY
secrets="$(canopy compose exec -T api python -c "
import json; print(json.load(open('/agent-data/config/export.json'))['secrets'])")"
grep -q POSTGRES_PASSWORD <<<"$secrets"

say "Destroy everything but the target"
canopy compose down -v --remove-orphans >/dev/null 2>&1
wipe agent-data
wipe data
[ ! -e "$CANOPY_DATA" ] && [ ! -e "$CANOPY_AGENT_DATA" ]

say "Clean install, restore"
canopy init >/dev/null
target
canopy restore

say "Check"
[ "$(psql "SELECT description FROM workspace.namespaces WHERE name = 'proof'")" = "survives a restore" ]
[ "$(cat "$CANOPY_DATA/api/token")" = "$api_token" ]
[ "$(canopy token)" = "$agent_token" ]
[ "$(canopy servers)" = "$before" ]
grep -q "proof skill" "$CANOPY_AGENT_DATA/skills/proof/SKILL.md"
grep -q "proof note" "$CANOPY_AGENT_DATA/notes/proof.md"
grep -q "Proof repos go in the proof org" "$CANOPY_DATA/context/preferences/proof-rule.md"
git -C "$CANOPY_DATA/context" -c safe.directory='*' log --format=%s | grep -q "Add preference proof-rule"
grep -q "POSTGRES_PASSWORD=proof-postgres" "$CANOPY_AGENT_DATA/secrets.env"
canopy compose exec -T db sh -c 'PGPASSWORD=proof-canopy psql -h 127.0.0.1 -U canopy -d canopy -Atc "SELECT 1"' >/dev/null
# The clones are not backed up, so status stays red until the seed's sync has rebuilt them.
for _ in $(seq 60); do canopy status >/dev/null 2>&1 && break; sleep 2; done
canopy status | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('status ok:', d['ok'], d['components']['catalogue'])
sys.exit(0 if d['ok'] else 1)"

say "The restored stack can back up again"
mark="$(last_run)"
canopy jobs run backup-data >/dev/null
job_ok backup-data "$mark"
job_ok restore-test "$mark"
say "PASSED"
