---
name: diagnose
description: Work out why a Canopy skill or tool cannot be found, or why the server is unhealthy. Use on "skill not found", "tool missing", "Canopy is down", or "search returns nothing".
---

# Diagnose Canopy

Start with `catalogue__health`. Each entry in `problems` names its cause. If it cannot be called:

```bash
canopy status
canopy compose ps
canopy compose logs --tail 50 api gateway catalogue seed
```

| Symptom | Check |
| --- | --- |
| 403 from every endpoint | The gateway is not initialised: the seed has not finished. Read its logs. |
| 401 | Wrong token. Print it again with `canopy token`. |
| `cannot read the API token` | The stack has not started, or `canopy init` was skipped and Docker made the folder as root. |
| Empty `results` and an `error` about an empty index | A sync has not finished, or failed. Read `last_sync` in health. |
| A skill is missing | `catalogue__list` with `plugin=<name>`. If the plugin is absent, its marketplace is not configured or its entry was skipped: read `last_sync.summary.errors`. |
| A tool is missing | `toolbox__list_tools` with `server=<name>`. If the server is absent, read the seed logs. Then run `catalogue__sync`. |
| A skill calls a server that is not there | `catalogue__mcp_servers` with `problems_only=true`. |
| `degraded` in search results | Semantic search is off or failing, and results are keyword only. Check `EMBED_URL`. |
| Seed says the admin token is gone | Restore the backup, as in the `backup-restore` skill. |

An empty search is only a miss if health is `ok`.
