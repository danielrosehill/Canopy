# Target design

Status: **proposed, 2026-11-05. Nothing here is built unless it says so.** For what runs today, see
[architecture](architecture.md). This page is the shape Canopy is moving to, and the order of work.

## The idea in one paragraph

Canopy is one system, not a gateway with parts bolted on. Agents reach it through one connector and
search for what they need. Operators and their agents reach it through one API. Everything the user
owns sits in one folder, everything rebuildable sits in another, and one scheduler runs every
recurring job. MCPJungle stays unmodified, behind the API, and is upgraded only after a contract test.

## Rules every part must keep

Learned from earlier skill indexes that failed:

- **Failures are loud.** An empty index, failed auth or failed sync never looks like "no results".
- **Listing and reading work without search,** so a broken embedder does not hide a skill.
- **The index rebuilds from git.** Vectors are the only costly part, so they are backed up, but
  losing them costs an embedding run, not data.
- **Cost is incremental.** Adding a few plugins embeds a few records, and an idle day costs nothing.

## Services

| Service | Job | Profile | State |
| --- | --- | --- | --- |
| `gateway` | MCPJungle, unmodified and pinned | core | Postgres |
| `db` | Postgres: gateway, plus jobs and memory | core | `data/postgres` |
| `catalogue` | Skill and plugin index, keyword half of search | core | `data/catalogue` (cache) |
| `toolbox` | Long-tail tool calls | core | None |
| `api` | Control API, scheduler, dashboard | core | Postgres |
| `seed` | One-shot: applies config through the same code as the API | core | None |
| `ollama` | Local embeddings, model pulled on first start | `search` | `data/ollama` |
| `qdrant` | Vectors for every collection | `search` | `data/qdrant` |
| `storage` | Garage: S3-compatible staging | `storage` | `data/storage` |
| `memory` | General memory, MCP and API | `memory` | Postgres, Qdrant |
| `cache` | Valkey | `cache` | None |
| `tunnel`, `tailnet` | Remote access | as named | `data/tailnet` |

`.env.example` sets `COMPOSE_PROFILES=search,storage`, so a default install has hybrid search and
staging. `core` alone is the small install, with keyword search. `search` needs about 2 GB of RAM.

`ui` stops being a service: the API serves the dashboard. `backup` stops being a service: it becomes a
job. That leaves one process to secure and one port to expose.

## Two folders

Bind mounts rather than named volumes, so an agent on the host can read them and a backup is a path.

```
$CANOPY_AGENT_DATA   (default ~/canopy/agent-data)   Yours. Backed up. Agents may write here.
  config/            marketplaces.json, servers.json, jobs.json, backup.json
  secrets.env        Never pushed anywhere in the clear. Optional if secrets come from the environment.
  skills/            Your private skills, indexed as a local marketplace
  notes/             What agents learn about this deployment

$CANOPY_DATA         (default ~/canopy/data)         Canopy's. Agents do not edit this by hand.
  postgres/  qdrant/  ollama/  catalogue/  storage/  tokens/  backups/
```

This replaces `CANOPY_CONFIG_DIR` and the overlay directory. An overlay is now just an `agent-data`
folder, plus an optional `compose.override.yaml` inside it for private services.

**Public plugin, private user area.** Plugin directories are replaced on update, so a plugin must not
keep user state there. `${CLAUDE_PLUGIN_DATA}` survives updates, but is deleted on uninstall, exists
per machine, and is not substituted in Bash-tool commands. So the management plugin stores only a
pointer to `agent-data` there, and its skills read and write through the API or that path.

## Control API

One service and one token, at `http://api:8090` on the stack network and `127.0.0.1:8090` on the
host. Everything that manages Canopy goes through it, so the skills, the dashboard and the `canopy`
CLI are all clients of one contract.

| Route | Does | Fronts |
| --- | --- | --- |
| `GET /v1/status` | Health of every component, in one reply | All |
| `/v1/gateway/servers`, `/groups`, `/tools` | List, add, remove, verify | MCPJungle REST |
| `/v1/config` | Read, validate, diff, apply | `agent-data/config` |
| `/v1/catalogue/search`, `/records/{id}`, `/marketplaces`, `/sync` | Search, read, manage | `catalogue` |
| `POST /v1/embeddings` | OpenAI-compatible embeddings | Ollama, fallback |
| `/v1/embeddings/pending`, `/run` | Incremental embedding | `catalogue`, `memory` |
| `/v1/vectors/{collection}` | Upsert, search, stats | Qdrant |
| `/v1/memory/...` | Namespaces, remember, recall | `memory` |
| `/v1/storage/stage`, `/buckets` | Upload, presigned URL, expiry | `storage` |
| `/v1/jobs`, `/jobs/{id}/run`, `/jobs/{id}/history` | Inspect and trigger | Scheduler |
| `/v1/backup`, `/backup/config` | Run, list, plan a restore | Backup job |
| `/v1/update/check`, `/update/plan` | Compare versions, read compatibility | `compat.json` |

Rules:

- **Only the API holds the gateway's admin token.** `/admin` is no longer mounted elsewhere.
- **Every gateway write is verified by reading back.** A registration can report a timeout although
  it worked, so that check lives here, once.
- **No Docker socket.** That would make the API root on the host. So `update/plan` says what to do, and
  the `canopy` CLI on the host does it.
- **No management MCP.** This replaces that plan. Managing a server needs a shell, so a skill with the
  CLI is the better route, and it keeps admin operations out of every agent's tool list and out of
  reach of the toolbox. Hosted clients do not manage the server.
- The API needs its own token even with the gateway in development mode.

## One embedder, one vector store

Every embedding goes through `POST /v1/embeddings`. The catalogue and memory services are pointed at
it as a normal OpenAI-compatible endpoint.

- **Primary and fallback.** Local Ollama first, with an optional hosted fallback for the same
  model. If the primary does not answer inside a budget, the fallback serves that request.
- **One model label.** Vectors are stored under a canonical name (`bge-m3`) whichever route produced
  them, with a table mapping that name to each provider's own (`baai/bge-m3`). Today, changing provider
  marks every vector stale, because the label is the provider's name.
- **Qdrant holds all vectors.** Point ids are derived from the content hash, so embedding stays
  incremental and re-running is safe. Collections: `catalogue`, `memory`.

The reason is one store, one embedder and one backup, not speed: at 5,000 records the current
SQLite scan is fast enough. Qdrant earns its place once memory shares it. If Qdrant is down, search
falls back to keyword and says so, as it does when embeddings fail today.

## Storage

[Garage](https://garagehq.deuxfleurs.fr), pinned at v2.5.0, in the `storage` profile. MinIO's community
edition is no longer an option: images stopped in October 2025 and the repo is archived.

Tested on 2026-11-05 against SeaweedFS 4.52, which also passed every functional test. Garage won on size:
6 MiB of RAM at idle against 72 MiB, and a 69 MB image against 780 MB, for what is a small staging
store. It runs as a separate unmodified container, so its licence (AGPL-3.0, to be confirmed) does not
touch Canopy's MIT.

- **Bootstrap.** The image has no shell, so the `api` service does the layout, buckets and keys
  through Garage's admin API. That route is not yet tested: the commands were tested through its CLI.
- **Region.** Set `s3_region = "us-east-1"`, or presigned URLs from standard clients fail.
- **Host.** A presigned URL only works for the hostname it was signed with, on both stores tested. So
  `/v1/storage/stage` takes the audience (a stack service, or a remote client) and signs for that
  name.
- **Expiry.** A transient bucket has a lifecycle rule. Garage accepted the rule and returned it, but no
  deletion has been observed yet, so the `storage-expiry` job checks that it happens.
- **Console.** Garage has none. The dashboard shows buckets and use.

## Memory

A general memory with namespaces, as an opt-in service. Postgres is the source of truth and Qdrant
the index, so the index can always be rebuilt. It exposes an MCP server for agents and routes on the
API for operators. Tools: `remember`, `recall`, `list_memories`, `list_namespaces`, `update_memory`,
`forget`.

## Scheduler

One scheduler, inside `api`, replaces the catalogue's own loop, the backup sleep loop and any host
timers. Definitions live in `agent-data/config/jobs.json`, and each run is recorded with its duration,
result and output, which is what the dashboard shows.

| Job | Default | Does |
| --- | --- | --- |
| `sync` | Hourly | Index marketplaces and gateway tools |
| `embed` | After `sync` | Embed pending records |
| `health` | 5 min | Refresh `/v1/status`, flag upstreams that stopped answering |
| `tool-refresh` | Daily | Re-read upstream tool lists, which the gateway freezes at registration |
| `backup-data` | Daily | Incremental encrypted backup |
| `backup-config` | Weekly, and on change | Push config only |
| `storage-expiry` | Daily | Confirm expiry ran on transient buckets |
| `update-check` | Daily | Report newer versions. Never applies them |

A job takes a lock, so two runs never overlap. A missed run starts on the next boot.

## Cache

Valkey, opt-in, and built late. Measured on a real deployment: a warm query embedding takes 40 to 80 ms
and a cold model load about 1.6 s, so a cache adds little in normal use. It helps in three places:
repeat queries while a bulk embed saturates the CPU, the `/v1/status` snapshot, and tool lists. Each use
goes through an interface that falls back to no cache, so the stack never depends on it.

## Dashboard

Served by the API at `/`, and only a client of it. One page: every component's health, upstreams with
tool counts and last answer, job history, embed backlog, index counts, storage use, age of the
last backup, available updates. The skill picker moves here. It has no login of its own, so it binds
to localhost and is exposed only through Cloudflare Access or the tailnet.

## Backup

| Tier | Contains | Default | Enough for |
| --- | --- | --- | --- |
| Config | `agent-data/config`, private skills and notes, names of secrets but not their values | Weekly and on change | Rebuilding the install |
| Data | Postgres dump, tokens, Qdrant snapshot, `secrets.env` | Daily, incremental | Restoring it exactly |

Both use [restic](https://restic.net) to any S3-compatible target, encrypted by default. `backup.json`
names the target. The catalogue and Ollama models are left out, because they rebuild themselves. A
backup is not trusted until a restore test has passed, and that test is itself a job.

## Updates

MCPJungle is the piece we do not control. Canopy depends on specific behaviour of it, listed in
[architecture](architecture.md#gateway-behaviour-the-seed-depends-on).

1. Every image is pinned. Nothing floats, and auto-updaters are off for Canopy's containers.
2. `compat.json` lists the MCPJungle versions Canopy has been tested against.
3. A scheduled CI job watches MCPJungle releases, starts the real stack on the new version, and runs
   the **contract suite**: one test per behaviour we rely on, including identities, group rebuilds,
   registration, tool listing and enterprise mode. A pass opens a PR that bumps the pin and `compat.json`.
   A failure opens an issue naming the behaviour that changed.
4. On a server, the `update` skill runs `canopy update`: backup, pull, apply, status check, search
   comparison, and restore on failure. MCPJungle migrates its database on start, so rollback means
   restoring the dump, not only changing the tag.

Canopy ships no Watchtower: the original is archived, and an auto-updater would skip step 3.

## Connecting a client

Two plugins with different audiences, in the marketplace published from this repo:

| Plugin | Installed on | Holds |
| --- | --- | --- |
| `canopy-connector` | Every device, at user level, and the only connector needed | MCP connection, skills to search, load and validate |
| `canopy` | The server where the agent runs | Management skills, using the `canopy` CLI |

```
/plugin marketplace add danielrosehill/Canopy
/plugin install canopy-connector@canopy
```

The connector also ships a short instruction snippet for the user's `CLAUDE.md`, because agents have to
be told to search before they conclude that a skill does not exist.

## Documentation

- `docs/manual.md`: the user manual. Install, connect, and above all **how finding skills works**,
  since that is the point of the system: what the connector skills do, how to search, how to load
  what you find, and how to call a long-tail tool.
- `docs/api.md`: every route, generated from the API.
- `docs/connect/`: a page per client (Claude Code, Codex, others).

## Order of work

Each step ends with a clean install on an empty machine, and the contract suite once it exists.

1. Folder layout, the `api` service, `/v1/status`, `canopy` CLI, gateway writes moved behind the API.
2. Contract suite in CI, with `compat.json`. Early, because every later step changes what it protects.
3. Embedder route, Ollama and Qdrant in `search`, catalogue vectors moved to Qdrant.
4. Scheduler, then backup and restore as jobs.
5. Storage profile, stage route and skill.
6. Connector plugin, manual and API reference.
7. Dashboard.
8. Memory.
9. Cache, if measurement still supports it.
10. Stable ids.

Enterprise mode stays the shipped default throughout.
