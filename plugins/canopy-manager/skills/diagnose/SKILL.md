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
| `degraded` in search results | Each entry names what fell short. `keyword only`: check `EMBED_PROVIDER`. `fused order`: the reranker failed or timed out, so check `RERANK_MODEL`. `keyword index is not built yet`: the catalogue has just started, so wait a few seconds. |
| Search is slow | `canopy api POST /v1/search '{"query":"...","trace":true}'` gives each stage's time and outcome. |
| Unhealthy straight after an update | `canopy update` goes back by itself. If it reports `STILL NOT HEALTHY`, use the `update` skill. |
| `jobs` is red in `canopy status` | `failing` holds each job's last error, and `canopy jobs runs NAME` its history. `overdue` means no success for two intervals. An `error` about ticking means the scheduler has stopped: restart `api`. |
| No alert arrived | Alerts need `CANOPY_ALERT_WEBHOOK`, and come only from the `health` job, when the failing set changes. `canopy compose logs api` shows `alert not sent`. |
| Seed says the admin token is gone | Restore the backup, as in the `backup-restore` skill. |

An empty search is only a miss if health is `ok`.
