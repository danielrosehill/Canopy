# Architecture

Verified against MCPJungle 0.4.7 on 2026-11-05, on a clean Docker host.

## Services

One image, `ghcr.io/danielrosehill/canopy`, runs each Python service with a different command. The
`cli` container has its own tag in the same package.

| Service | Module | Reachable from | State |
| --- | --- | --- | --- |
| `gateway` | MCPJungle `0.4.7-stdio` | `127.0.0.1:8080` | Postgres |
| `db` | `postgres:17-alpine` | Stack network | `data/postgres` |
| `catalogue` | `canopy.catalogue`, `canopy.index` | Stack network | `data/catalogue` (a cache) |
| `toolbox` | `canopy.toolbox` | Stack network | None |
| `api` | `canopy.api`, `canopy.control` | `127.0.0.1:8090` | `data/api`, `data/admin`, `data/tokens` |
| `seed` | `canopy.seed` | One-shot, a client of `api` | None |
| `cli` | `ghcr.io/danielrosehill/canopy:cli-*`, Alpine | `canopy run` | `agent-data/cli` |
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
The `backup` profile also mounts `data/admin`, read-only, until backup becomes a job of the API.

## Start order

Services start before tokens exist, so they read token files on every use. The API starts once
`catalogue` and `toolbox` are healthy. The seed waits for the API, and calls `POST /v1/config/apply`, which:

1. Initialises the gateway and creates identities, if needed.
2. Registers built-in and configured upstreams whose expanded config changed.
3. Rebuilds groups, if a server was registered or a group definition changed.
4. Asks the catalogue to sync, so tool records appear straight away.

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

## Gateway behaviour the seed depends on

- A group update adds only servers it has not seen, so groups are deleted and created again.
- A group create fails if it names an unregistered server, or if it resolves to no tools. Membership
  is checked before the delete, and the old definition is restored if the create still fails.
- A new server does not join an existing group by itself.
- A registration can report a timeout although it worked, so a failure is believed only if the server
  list agrees.
- Writes use the REST API. The gateway's CLI is in a different image.
- `GET /api/v0/tools` lists tools for an admin token.

## Search

`catalogue__search` fuses BM25 (SQLite FTS5) and cosine similarity with reciprocal rank fusion. With
no `EMBED_URL`, or if the embeddings call fails, results are keyword only and `degraded` says so.

Embeddings use any OpenAI-compatible `/embeddings` endpoint, and are keyed by content hash and model.
A sync does not embed unless `EMBED_ON_SYNC=true`. `catalogue__embed` reports pending records and
estimated tokens, and `dry_run=false` starts the run.

## Marketplaces are untrusted input

Canopy clones what a manifest names, so the indexer:

- accepts only `owner/repo` or `https://` sources, and sets git's `protocol.allow=never` otherwise
- rejects plugin and marketplace names that could be a path, since they become directories
- rejects refs that could be read as git options
- rejects `..` in plugin paths
- sets `core.symlinks=false`, so a symlink in a clone cannot point a `SKILL.md` at a token
- keeps side-file reads inside the plugin root

Indexing never runs plugin code. Skills are still instructions your agent will follow.
