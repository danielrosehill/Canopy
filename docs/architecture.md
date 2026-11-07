# Architecture

For the reasoning behind the design, and diagrams, start with the [overview](overview.md).

Verified against MCPJungle 0.4.7 on 2026-11-05, on a clean Docker host.

## Services

One image, `danielrosehill/canopy` on Docker Hub, with a mirror at `ghcr.io/danielrosehill/canopy`, runs each Python service with a different command. The
`cli` container has its own tag in the same package.

| Service | Module | Reachable from | State |
| --- | --- | --- | --- |
| `gateway` | MCPJungle `0.4.7-stdio` | `127.0.0.1:8080` | Postgres |
| `db` | `pgvector/pgvector:0.8.1-pg17` | Stack network | `data/postgres` |
| `catalogue` | `canopy.catalogue`, `canopy.index` | Stack network | `data/catalogue` (a cache) |
| `toolbox` | `canopy.toolbox` | Stack network | None |
| `api` | `canopy.api`, `canopy.control` | `127.0.0.1:8090` | `data/api`, `data/admin`, `data/tokens` |
| `seed` | `canopy.seed` | One-shot, a client of `api` | None |
| `cli` | `danielrosehill/canopy:cli-*`, Alpine | `canopy run` | `agent-data/cli` |
| `ui` | `canopy.ui` | `127.0.0.1:8081` | None |

Agents connect to `/v0/groups/core/mcp`. The `core` group always holds `catalogue` and `toolbox`.

## Identities

The gateway has two kinds of identity, and neither can do the other's job. Confirmed by test:

| Token | MCP endpoints | `GET /api/v0/servers` | Admin writes |
| --- | --- | --- | --- |
| MCP client | Yes, per allow-list | 401 | No |
| User | 401 | Yes | No |
| Admin | Not tested | Yes | Yes |

So the API creates:

| Identity | Kind | Token file | Mounted in |
| --- | --- | --- | --- |
| admin | Admin, from `POST /init` | `/admin/admin` | API only |
| `canopy-agent` | Client, allow `*` | `/admin/agent` | API only, shown by `canopy token` |
| `canopy-services` | Client, allow `*` | `/tokens/services` | Services, read-only |
| `canopy-reader` | User | `/tokens/reader` | Services, read-only |

An enterprise gateway that has not been initialised answers 403 to everything except `/health`,
`/metadata` and `/init`. `POST /init` with `{"mode": "enterprise"}` returns the admin token once. A
second call returns 400. If `data/admin` is lost while the database survives, the token cannot be
recovered, and the API says so.

The API has its own token, `data/api/token`, needed on every route except `/health` and in every mode.

## Backup

Three jobs in `api`, from `canopy.backup`. They start once `agent-data/config/backup.json` exists:

```json
{"repository": "s3:https://s3.example.com/bucket/canopy", "region": "us-east-1",
 "options": {"s3.bucket-lookup": "path"}, "keep": {"daily": 14, "weekly": 8}}
```

`region`, `options` and `keep` are optional. The file holds no secrets. `RESTIC_PASSWORD`, `AWS_ACCESS_KEY_ID`
and `AWS_SECRET_ACCESS_KEY` come from the environment. **Keep a copy of `RESTIC_PASSWORD` outside the stack:**
`secrets.env` is inside the backup it unlocks, and restic cannot recover a lost password.

| Job | Default | Does |
| --- | --- | --- |
| `backup-data` | Daily | Dumps both databases, then stores them with `data/api`, `data/admin`, `data/tokens` and `secrets.env`, and applies retention |
| `backup-config` | Hourly | Writes `config/export.json`, then stores `config`, `skills`, `notes` and `compose.override.yaml`. A snapshot is made only if something changed |
| `restore-test` | After `backup-data` | `restic check`, then restores the latest backup beside the live stack: file hashes, and each dump into a scratch database with exact row counts |

Row counts are exact on a busy stack because each dump and its counts read one exported Postgres snapshot.
`api` holds `POSTGRES_PASSWORD` for this: `pgvector` is not a trusted extension, so a restore needs the
superuser. `agent-data/cli` (CLI logins) and the catalogue's clones and Ollama models are not backed up.

`canopy restore [--replace]` brings back the latest backup. Files go first, so `db` starts with the restored
passwords, then the databases. Before it runs, a clean install needs `backup.json`, the three secrets, and
any override that reaches the target. A database holding data is refused without `--replace`. After a restore,
`/v1/status` stays red until a sync has cloned the marketplaces again.

`tests/contract/backup.sh` proves all of this against a throwaway Garage, and runs in CI.

## Storage

The `storage` profile runs Garage, unmodified. `api` writes `data/storage-secrets/rpc_secret` and `admin_token`
at start, and `storage` starts after it. The first `POST /v1/storage/stage` or `GET /v1/storage/buckets` does
the rest through Garage's admin API on port 3903, which is not published: layout, the buckets in
`config/storage.json` (default `transient`, 1 day), one key in `s3.json`, and the expiry rules, each read back.
Every step is safe to repeat. Staged files and these secrets are not backed up: after a restore the same
bootstrap runs again.

`/v1/status` shows `storage` as `off`, `not used yet` or `running`, and red only if a store that was in use
stopped answering. `tests/contract/storage.sh` proves this on the real image, and runs in CI.

## Start order

Services start before tokens exist, so they read token files on every use. The API starts once
`catalogue` and `toolbox` are healthy. The seed waits for the API, and calls `POST /v1/config/apply`, which:

1. Initialises the gateway and creates identities, if needed.
2. Registers built-in and configured upstreams whose expanded config changed.
3. Rebuilds groups, if a server was registered or a group definition changed.
4. Deregisters upstreams it registered earlier that the config no longer lists. It never touches an upstream it did not
   register, one still in a group, or anything at all if the config is missing or lists no servers.
5. Asks the catalogue to sync, so tool records appear straight away.

The seed exits 1 if anything failed, and lists each failure.

## Folders

Bind mounts, so an agent on the host can read them and a backup is a path. `canopy init` makes them
as the current user. A file the API writes takes the owner of its folder, so that user can read tokens
and config. Docker creates a missing folder as root, so run `init` first. `data/postgres` and
`data/catalogue` end up owned by their containers, so deleting them needs `sudo` or Docker.

## Control API

Bearer token, JSON, on `127.0.0.1:8090`.

| Route | Does |
| --- | --- |
| `GET /health` | Liveness, with no token |
| `GET /v1/status` | Gateway, upstreams, groups, catalogue and toolbox, each checked for real. `ok` is false if any part fails, and upstreams or groups in the config but not the gateway are named |
| `GET`, `POST /v1/gateway/servers` | List, add. `?replace=true` overwrites |
| `DELETE /v1/gateway/servers/{name}` | Remove. Refused while a group names the server |
| `GET /v1/gateway/groups`, `PUT`, `DELETE /v1/gateway/groups/{name}` | List, build, remove |
| `GET /v1/gateway/tools` | List |
| `GET /v1/config`, `POST /v1/config/apply` | Read, apply |
| `GET /v1/tokens/agent` | The token for MCP clients. Null in development mode |

Writes run one at a time. Each is believed only once a read agrees, and only then saved to `servers.json`,
so a failed change leaves the config as it was. `${VAR}` stays in the file, and is expanded on the way
to the gateway. Built-in servers and the core group cannot be removed.

## Database

`scripts/db-init.sh` runs once, on an empty `data/postgres`: databases `mcpjungle` and `canopy`, each with
its own role, and neither role can connect to the other's. `canopy` has the `vector` extension. Tables
come from `src/canopy/migrations/NNN_name.sql`, applied by `canopy.store.migrate` and recorded in
`system.schema_version`. A data folder from before this layout has no such roles: start from an empty
folder.

## Gateway behaviour the seed depends on

Each line has a test in `tests/contract/`, run by CI against the real image, in both modes. Run it
yourself with `tests/contract/run.sh`, or `MCPJUNGLE_TAG=<tag> tests/contract/run.sh` for another
version. `compat.json` records what has passed. Measured on 0.4.7, 2026-11-05.

- A group update keeps any list it was not given. The stored definition then disagrees with the live
  group until the gateway restarts, so groups are deleted and created.
- A group create fails if it names an unregistered server (404), or if it resolves to no tools (400).
  Membership is checked before the delete, and the old definition is restored if the create still fails,
  times out without landing, or is cut off. If the current group cannot be read, nothing is changed.
- A group follows server names. A new server joins only a group that names it, and a named server
  that is removed and registered again returns to its groups. Canopy still rebuilds after a
  registration, as a precaution.
- A registration can report a timeout although it worked. The server is listed at once and its tools
  arrive later, so a failure is believed only if the server list agrees, and after a timeout the
  tools must arrive too.
- A second registration of a name fails, so `register` deletes first. Tool lists freeze at registration,
  and registering again is what refreshes them.
- An upstream that cannot be reached is refused, and is not listed. Since a replacement deletes first,
  that used to cost the working server: `register` now takes its definition from
  `GET /api/v0/server_configs` beforehand, and registers it again if the new one fails. That route is the
  only copy of the expanded credentials, and what it returns registers as it is.
- Enable and disable work on a server or a tool, and take it out of a group's tools. Registering again
  clears them, so a refresh disables again whatever was disabled.
- `GET /api/v0/tools?server=` lists one server's tools, each with `enabled`.
- Writes use the REST API. The gateway's CLI is in a different image.
- `GET /api/v0/tools` lists tools for an admin token.
- `/init` gives the admin token once. A client token works only on MCP endpoints, and a user token only
  reads REST (401 on MCP, 403 on a write). A lost token cannot be shown again, so the identity is replaced.
- Development mode needs no `/init` and no tokens.
- A group with nothing in it answers `tools/list` with `"tools": null`.

## Search

`canopy.retrieval.Search` is the search component, and the only way in. It runs inside `catalogue`, and
`catalogue__search` and `POST /v1/search` are two doors onto the same `run`:

| Stage | Does | If it fails or the budget is spent |
| --- | --- | --- |
| `filter` | Applies `kind`, `plugin`, `marketplace` | The search fails: a bad `kind` is the caller's mistake |
| `keywords` | BM25 from the keyword index | Says so in `degraded` if the index is not built yet |
| `vectors` | Embeds the query, exact pgvector search | Keyword only, in `degraded` |
| `fuse` | Reciprocal rank fusion, vectors counted three times (`SEARCH_VECTOR_WEIGHT`) | Cannot fail |
| `rerank` | Hosted rerank, with `RERANK_MODEL` set | Fused order, in `degraded` |
| `shape` | Builds the reply an agent reads | The search fails |

After the stages, the query and the ids shown go to `system.search_log`, kept `SEARCH_LOG_DAYS`. A failed
write is logged and costs the search nothing.

`POST /v1/search` adds two things the MCP tool leaves out, so they cost an agent no schema tokens:
`budget_ms`, a limit on the whole search that shortens the embedding and rerank timeouts and skips them when
spent, and `trace: true`, which returns each stage's time and outcome. It needs the API token, so it serves
the stack and its operators. The connector's hook stays on MCP: a client machine holds a gateway token, and
the API token can change the stack.

- Keywords: `canopy.ranking.KeywordIndex`, BM25 on [Tantivy](https://github.com/quickwit-oss/tantivy-py) in
  RAM, with field weights name 10, description 4, plugin 2, body 1, and English stemming. `refresh_keywords`
  compares each record's `hash` with what it indexed and changes only what differs, so a sync adding a skill
  costs milliseconds. A restart builds it again from Postgres, in about a second.
- Vectors: exact pgvector search over `catalogue.records.embedding`.

With `RERANK_MODEL` set, the top 20 candidates that are not tools go to the provider's `/rerank` with their
names and descriptions. If that fails or takes over `RERANK_TIMEOUT_SEC`, search returns the fused order.
Anything that falls short shows in `degraded`: no provider, a failed embedding, a failed rerank, or a
keyword index not built yet.

Embeddings go through `POST /v1/embeddings` on the API, the only place that knows the providers.
`EMBED_PROVIDER` is `local` (Ollama, the `search` profile) or `openrouter`, and `EMBED_FALLBACK` names a second
route. A single text is a query, so it gets a short budget (`EMBED_QUERY_BUDGET_SEC`) before the fallback.
Vectors are labelled with the canonical name (`bge-m3`), and `system.model_map` says what each provider
calls it. The column is `vector(1024)`, so a model of another size needs a migration.

A record keeps its vector while `embed_key`, the hash of the embedded text, stays the same. With
`EMBED_ON_SYNC=true` every sync embeds what it wrote, however it was started, and a record still missing a
vector afterwards fails `health`. Otherwise `canopy embed` reports pending records and estimated tokens,
and `dry_run=false` starts the run. Until it has a vector, a record ranks on its keyword position alone.

**Freshness.** `sync-recent` runs every 5 minutes, and `canopy sync` with no arguments does the same: one
GitHub call lists recent pushes, and only marketplaces and plugin repos pushed since the last pass are checked.
A marketplace push is how a plugin arrives or leaves, so pruning always runs. With no token, a failed call, or
a list that cannot cover the gap, it becomes a full pass. The hourly `sync` checks every plugin as the backstop.
A description changed only in a marketplace also reindexes that plugin.

`scripts/eval-search.py <queries.json>` scores a running stack. `tests/fixtures/search.json` is the generic
labelled set. Queries that name private skills stay in the operator's own `agent-data`.

## Marketplaces are untrusted input

Canopy clones what a manifest names, so the indexer:

- accepts only `owner/repo` or `https://` sources, and sets git's `protocol.allow=never` otherwise
- rejects plugin and marketplace names that could be a path, since they become directories
- rejects refs that could be read as git options
- rejects `..` in plugin paths
- sets `core.symlinks=false`, so a symlink in a clone cannot point a `SKILL.md` at a token
- keeps side-file reads inside the plugin root

Indexing never runs plugin code. Skills are still instructions your agent will follow.
