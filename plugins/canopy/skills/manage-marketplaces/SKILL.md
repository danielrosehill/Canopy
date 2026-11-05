---
name: manage-marketplaces
description: Add or remove a Claude Code plugin marketplace on a Canopy server. Use on "add this marketplace", "index this repo", or "remove a marketplace".
---

# Manage marketplaces

Marketplaces are listed in `marketplaces.json`, in `CANOPY_CONFIG_DIR` (default `./config`):

```json
{ "marketplaces": [ { "name": "my-plugins", "repo": "owner/repo" } ] }
```

`repo` is a GitHub `owner/repo` or an `https://` git URL. `name` is optional and defaults to the repo
name. The repo must have `.claude-plugin/marketplace.json`. Add `"ref"` to pin a branch or tag. A
private repo needs `GITHUB_TOKEN` in `.env`, then `docker compose up -d catalogue`.

To add or remove one:

1. Edit the file. The catalogue reads it on every sync, so nothing needs restarting.
2. Call `catalogue__sync`. Removing a marketplace drops its plugins from the index on this sync.
3. Poll `catalogue__health` until `sync_running` is false, and check `last_sync.summary.errors`.
4. Confirm with `catalogue__list` and `kind="plugin"`, `marketplace="<name>"`.

Plugin names are global. If two marketplaces list the same name, the later one wins and the sync
reports an error. New records have no embeddings until the `sync-and-embed` skill is run.
