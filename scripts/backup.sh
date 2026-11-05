#!/bin/sh
# Back up what cannot be rebuilt: both databases and the stack's tokens. Vectors are in the
# `canopy` database, so they are kept. The clones are left out: they come back from git.
set -eu
while :; do
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  dir="/backups/$stamp.partial"
  mkdir -p "$dir"
  chmod 700 "$dir"
  pg_dump -h db -U postgres -d mcpjungle -Fc -f "$dir/gateway.dump"
  pg_dump -h db -U postgres -d canopy -Fc -f "$dir/canopy.dump"
  tar -czf "$dir/tokens.tar.gz" -C / admin tokens
  pg_restore --list "$dir/gateway.dump" > /dev/null   # refuse an unreadable dump
  pg_restore --list "$dir/canopy.dump" > /dev/null
  mv "$dir" "/backups/$stamp"
  echo "backup $stamp done"
  ls -1d /backups/*Z | sort -r | tail -n +"$((BACKUP_KEEP + 1))" | xargs -r rm -rf
  sleep "$BACKUP_INTERVAL_SEC"
done
