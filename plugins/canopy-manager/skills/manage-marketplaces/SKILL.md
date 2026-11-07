---
name: manage-marketplaces
description: Add or remove a Claude Code plugin marketplace on a Canopy server. Use on "add this marketplace", "index this repo", or "remove a marketplace".
---

# Manage marketplaces

Marketplaces are listed in `agent-data/config/marketplaces.json`:

```json
{ "marketplaces": [ { "name": "my-plugins", "repo": "owner/repo" } ] }
```

`repo` is a GitHub `owner/repo` or an `https://` git URL. `name` is optional and defaults to the repo
name. The repo must have `.claude-plugin/marketplace.json`. Add `"ref"` to pin a branch or tag. A
private repo needs `GITHUB_TOKEN` in `agent-data/secrets.env`, then `canopy compose up -d catalogue`.

To add or remove one:

1. `canopy add-marketplace owner/repo [NAME]` or `canopy remove-marketplace NAME`. Each starts a full sync,
   and removing a marketplace drops its plugins from the index on that sync. To pin a `ref`, use
   `canopy api POST /v1/catalogue/marketplaces '{"repo":"owner/repo","ref":"v2"}'`. `canopy marketplaces` lists them.
2. If you edit the file by hand instead, nothing needs restarting, but run `canopy sync --full`: a quick pass does
   not read the list.
3. Poll `catalogue__health` until `sync_running` is false, and check `last_sync.summary.errors`.
4. Confirm with `catalogue__list` and `kind="plugin"`, `marketplace="<name>"`.

Plugin names are global. If two marketplaces list the same name, the later one wins and the sync
reports an error. New records have no embeddings until the `sync-and-embed` skill is run.
