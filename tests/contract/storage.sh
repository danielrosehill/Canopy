#!/usr/bin/env bash
# Proves the storage profile on a clean install, with the real Garage image: the admin-API bootstrap,
# staging, a URL signed for each audience, the expiry rule, and that a restart keeps it all.
set -euo pipefail
cd "$(dirname "$0")/../.."

base="${STORAGE_PROOF_DIR:-$HOME/.cache}"   # not /tmp, which can be RAM
mkdir -p "$base"
root="$(mktemp -d "$base/canopy-storage-proof.XXXXXX")"
export COMPOSE_PROJECT_NAME=canopy-storage-proof CANOPY_TAG=proof COMPOSE_PROFILES=storage
export CANOPY_AGENT_DATA="$root/agent-data" CANOPY_DATA="$root/data"
export CANOPY_PORT=18190 CANOPY_API_PORT=18191 CANOPY_API_URL=http://127.0.0.1:18191 CANOPY_STORAGE_PORT=18192
canopy() { clients/canopy "$@"; }
say() { printf '\n== %s\n' "$*"; }
field() { python3 -c "import json, sys; print(json.load(sys.stdin)$1)"; }

cleanup() {
  canopy compose down -v --remove-orphans >/dev/null 2>&1 || true
  docker run --rm -v "$root/..:/p" alpine:3.22 rm -rf "/p/$(basename "$root")" || true
}
trap cleanup EXIT

say "Install with the storage profile"
canopy init >/dev/null
cat > "$CANOPY_AGENT_DATA/compose.override.yaml" <<YAML
services:
  storage:
    labels: {com.centurylinklabs.watchtower.enable: "false"}
YAML
canopy compose build api cli >/dev/null
canopy up

say "Status before first use"
for _ in $(seq 30); do
  state="$(canopy api GET /v1/status | field "['components']['storage'].get('state')")"
  [ "$state" = "not used yet" ] && break
  sleep 1
done
echo "$state"; [ "$state" = "not used yet" ]

say "Stage a file: the first use bootstraps Garage"
file="$root/proof file.txt"
head -c 200000 /dev/urandom > "$file"
remote="$(canopy stage "$file")"
echo "$remote" | field "['key']"
url="$(echo "$remote" | field "['url']")"
grep -q "^http://127.0.0.1:18192/transient/" <<<"$url"
curl -sf "$url" | cmp - "$file"

say "A URL for the stack works from inside it, and not with its host changed"
stack="$(canopy stage "$file" --audience stack --expires 600 | field "['url']")"
grep -q '^http://storage:3900/' <<<"$stack"
canopy compose exec -T api python -c "
import hashlib, sys, httpx
print(hashlib.sha256(httpx.get(sys.argv[1]).raise_for_status().content).hexdigest())" "$stack" > "$root/inside"
sha256sum "$file" | cut -d' ' -f1 | cmp - "$root/inside"
code="$(curl -s -o /dev/null -w '%{http_code}' "${url/127.0.0.1/localhost}")"
echo "wrong host: $code"; [ "$code" = 403 ]

say "Refusals"
if canopy stage "$file" --expires 999999 >/dev/null; then echo "an expiry past the rule passed"; exit 1; fi
if canopy stage "$file" --bucket nope >/dev/null; then echo "an unknown bucket passed"; exit 1; fi

say "Buckets, the expiry rule and its job"
canopy buckets | tee "$root/buckets"
[ "$(field "[0]['objects']" < "$root/buckets")" = 2 ]
canopy compose exec -T api python - <<'PY'
from datetime import datetime, timedelta, timezone
from canopy.control import ControlError
from canopy.storage import Storage
s = Storage()
print(s.expiry_job())
try:
    s.expiry_job(datetime.now(timezone.utc) + timedelta(days=30))
except ControlError as e:
    print("late object caught:", e)
else:
    raise SystemExit("an overdue object passed")
PY

say "Restart: the data, key and layout survive, and a second bootstrap changes nothing"
key_before="$(docker run --rm -v "$CANOPY_DATA/storage-secrets:/s:ro" alpine:3.22 cat /s/s3.json | field "['key']")"
canopy compose restart api storage >/dev/null
for _ in $(seq 30); do canopy api GET /health >/dev/null 2>&1 && break; sleep 1; done
for _ in $(seq 30); do canopy buckets > "$root/buckets" 2>/dev/null && break; sleep 1; done
[ "$(field "[0]['objects']" < "$root/buckets")" = 2 ]
curl -sf "$url" | cmp - "$file"
key_after="$(docker run --rm -v "$CANOPY_DATA/storage-secrets:/s:ro" alpine:3.22 cat /s/s3.json | field "['key']")"
[ "$key_before" = "$key_after" ]

say "Storage proof passed"
