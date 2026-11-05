# Architecture

Verified against MCPJungle 0.4.7 on 2026-11-05, on a clean Docker host.

## Services

One image, `ghcr.io/danielrosehill/canopy`, runs each Python service with a different command.

| Service | Module | Reachable from | State |
| --- | --- | --- | --- |
| `gateway` | MCPJungle `0.4.7-stdio` | `127.0.0.1:8080` | Postgres |
| `db` | `postgres:17-alpine` | Stack network | Volume `db` |
| `catalogue` | `canopy.catalogue`, `canopy.index` | Stack network | Volume `catalogue` (a cache) |
| `toolbox` | `canopy.toolbox` | Stack network | None |
| `seed` | `canopy.seed` | One-shot | Volumes `admin`, `tokens` |
| `ui` | `canopy.ui` | `127.0.0.1:8081` | None |

Agents connect to `/v0/groups/core/mcp`. The `core` group always holds `catalogue` and `toolbox`.

## Identities

The gateway has two kinds of identity, and neither can do the other's job. Confirmed by test:

| Token | MCP endpoints | `GET /api/v0/servers` | Admin writes |
| --- | --- | --- | --- |
| MCP client | Yes, per allow-list | 401 | No |
| User | 401 | Yes | No |
| Admin | Not tested | Yes | Yes |

So the seed creates:

| Identity | Kind | Token file | Mounted in |
| --- | --- | --- | --- |
| admin | Admin, from `POST /init` | `/admin/admin` | Seed only |
| `canopy-agent` | Client, allow `*` | `/admin/agent` | Seed only |
| `canopy-services` | Client, allow `*` | `/tokens/services` | Services, read-only |
| `canopy-reader` | User | `/tokens/reader` | Services, read-only |

An enterprise gateway that has not been initialised answers 403 to everything except `/health`,
`/metadata` and `/init`. `POST /init` with `{"mode": "enterprise"}` returns the admin token once. A
second call returns 400. If the `admin` volume is lost while the database survives, the token cannot
be recovered, and the seed says so.

## Start order

Services start before tokens exist, so they read token files on every use. The seed waits for the
gateway and for healthy `catalogue` and `toolbox` containers, then:

1. Initialises the gateway and creates identities, if needed.
2. Registers built-in and configured upstreams whose expanded config changed.
3. Rebuilds groups, if a server was registered or a group definition changed.
4. Asks the catalogue to sync, so tool records appear straight away.

The seed exits 1 if anything failed, and lists each failure.

## Gateway behaviour the seed depends on

- A group update adds only servers it has not seen, so groups are deleted and created again.
- A group create fails if it names an unregistered server, or if it resolves to no tools. Membership
  is checked before the delete, and the old definition is restored if the create still fails.
- A new server does not join an existing group by itself.
- A registration can report a timeout although it worked, so a failure is believed only if the server
  list agrees.
- Writes use the REST API. The gateway's CLI is in a different image.

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
