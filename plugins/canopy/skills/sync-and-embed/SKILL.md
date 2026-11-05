---
name: sync-and-embed
description: Trigger a Canopy sync or an incremental embedding run, and check the cost first. Use on "sync the catalogue", "reindex", "run embeddings", or "update embeddings".
---

# Sync and embed

**Sync** pulls marketplaces and refreshes the gateway's tools. It runs every hour by itself.

- Call `catalogue__sync`. Pass `plugin` for just one, and `force=true` to reindex unchanged commits.
- Poll `catalogue__health` until `sync_running` is false.

**Embedding** is a separate step, so that a sync never spends money by surprise. It needs `EMBED_URL`
in `agent-data/secrets.env`. Without it, search is keyword only, and works.

1. Call `catalogue__embed` with no arguments. This is a dry run: it reports pending records and
   estimated tokens.
2. On a hosted API, work out the cost from the provider's price and show it to the user. Get their
   agreement before going on.
3. Call `catalogue__embed` with `dry_run=false`.
4. Poll `catalogue__embed` until `running` is false, and check `last.error`.

It is incremental: records are keyed by content hash, so only new or changed text is embedded.
Changing `EMBED_MODEL` makes everything pending again.
