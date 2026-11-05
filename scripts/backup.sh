#!/bin/sh
# Back up what cannot be rebuilt: the gateway database and the stack's tokens.
# The catalogue is left out because it is a cache of git and the gateway.
set -eu
while :; do
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  dir="/backups/$stamp.partial"
  mkdir -p "$dir"
  chmod 700 "$dir"
  pg_dump -h db -U canopy -d canopy -Fc -f "$dir/gateway.dump"
  tar -czf "$dir/tokens.tar.gz" -C / admin tokens
  pg_restore --list "$dir/gateway.dump" > /dev/null   # refuse an unreadable dump
  mv "$dir" "/backups/$stamp"
  echo "backup $stamp done"
  ls -1d /backups/*Z | sort -r | tail -n +"$((BACKUP_KEEP + 1))" | xargs -r rm -rf
  sleep "$BACKUP_INTERVAL_SEC"
done
