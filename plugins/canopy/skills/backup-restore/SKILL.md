---
name: backup-restore
description: Back up and restore a Canopy server. Use on "back up Canopy", "restore Canopy", or moving a server to another machine.
---

# Back up and restore

Only the gateway database and the stack's tokens need a backup. The catalogue is a cache of git and
the gateway, and a sync rebuilds it, apart from read counts.

Start scheduled backups, into `CANOPY_BACKUP_DIR` (default `./backups`):

```bash
docker compose --profile backup up -d backup
```

Backups hold tokens, so keep them private. Each dump is checked as readable before it is kept.

To restore, confirm with the user first, because this replaces the gateway's data. `<stamp>` is a
backup directory:

```bash
B=$PWD/backups/<stamp>
docker compose down
docker compose up -d db
docker compose run --rm --no-deps -T -v "$B:/restore:ro" -e PGPASSWORD=canopy \
  --entrypoint pg_restore backup -h db -U canopy -d canopy --clean --if-exists /restore/gateway.dump
docker compose run --rm --no-deps -T -v "$B:/restore:ro" \
  --entrypoint tar seed -xzf /restore/tokens.tar.gz -C /
docker compose up -d
```

The backup directories are owned by root, so read them through Docker or with `sudo`.

Use your `POSTGRES_PASSWORD` if you set one. Then check `catalogue__health`.
