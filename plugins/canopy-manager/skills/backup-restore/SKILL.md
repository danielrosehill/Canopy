---
name: backup-restore
description: Back up and restore a Canopy server. Use on "back up Canopy", "restore Canopy", or moving a server to another machine.
---

# Back up and restore

Backups are scheduler jobs using restic, encrypted, to any S3-compatible target. The catalogue's clones
and Ollama models are left out, because they rebuild themselves.

To start them, ask the user for a target, then write `agent-data/config/backup.json`:

```json
{"repository": "s3:https://s3.example.com/bucket/canopy", "region": "us-east-1"}
```

Put `RESTIC_PASSWORD`, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` in `secrets.env`, run `canopy up`,
and tell the user to keep a copy of `RESTIC_PASSWORD` somewhere else. Without it the backup is unreadable.

```bash
canopy jobs run backup-data       # restore-test follows it
canopy jobs runs restore-test     # a backup is not trusted until this passed
```

`canopy backups` lists the snapshots, and `canopy backup` starts one and waits for it, with no `restore-test` after.
`canopy status` reports the age of the last backup under `jobs`.

Before a restore, run `canopy backup-plan` and show the user what it would overwrite. It changes nothing.

To restore, confirm with the user first, because this stops the stack. On a clean install, run `canopy init`,
then put `backup.json` and the three secrets in place.

```bash
canopy restore             # refuses a database that already holds data
canopy restore --replace   # overwrites it
```

`canopy status` stays red until the following sync has cloned the marketplaces again. Then check
`catalogue__health`.
