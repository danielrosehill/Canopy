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
| `db` | Postgres with pgvector: the one store for gateway, records, vectors, jobs and memory | core | `data/postgres` |
| `catalogue` | Indexes marketplaces into Postgres, runs search | core | `data/catalogue` (git clones) |
| `toolbox` | Long-tail tool calls | core | None |
| `api` | Control API, scheduler, dashboard | core | Postgres |
| `seed` | One-shot: applies config through the same code as the API | core | None |
| `ollama` | Local embeddings, model pulled on first start | `search` | `data/ollama` |
| `storage` | Garage: S3-compatible staging | `storage` | `data/storage` |
| `memory` | General memory, MCP and API | `memory` | Postgres |
| `cli` | Alpine image holding the CLIs that skills use | core | `agent-data/cli` |
| `cache` | Valkey | `cache` | None |
| `tunnel`, `tailnet` | Remote access | as named | `data/tailnet` |

`.env.example` sets `COMPOSE_PROFILES=search,storage`, so a default install has hybrid search and
staging. `core` alone is the small install, with keyword search. `search` needs about 2 GB of RAM, for
Ollama and the model.

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
  cli/               Home directory of the `cli` container: CLI logins and settings

$CANOPY_DATA         (default ~/canopy/data)         Canopy's. Agents do not edit this by hand.
  postgres/  ollama/  catalogue/  storage/  tokens/  backups/
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

## Retrieval pipeline

Finding the right skill cheaply is the point of the system, so this part is designed from measurement.

```
git clone -> parse -> Postgres row: id, kind, name, description, full text, hash, vector
                                     |                                        |
                     read returns the text as written          search ranks on the vector and keywords
```

**One row holds both the readable text and its vector,** so they cannot drift apart, and a sync is one
transaction. Postgres with pgvector is the only store: no SQLite and no Qdrant. Memory uses the same
database. Only a record whose hash changed is embedded again.

**Search** fuses two rankings, with vectors weighted 3:1 over keywords:

- Vectors: exact pgvector search. At this size no approximate index is needed.
- Keywords: a small BM25 index the catalogue holds in memory, rebuilt from Postgres after a sync that
  changed something, with field weights and stemming. If it is rebuilding, search uses the previous one.

Measured on 2026-11-05: 50 labelled queries against a real index of 5,162 records, scored on where the
right skill landed among skills. With 50 queries, one query is 2 points, so small gaps are noise.

| Approach | First | Top 3 | Top 6 | MRR |
| --- | --- | --- | --- | --- |
| SQLite FTS5 and vectors, equal weight (what runs today) | 30 | 43 | 46 | 0.742 |
| SQLite FTS5 and vectors, vectors x2 | 35 | 44 | 46 | 0.797 |
| Vectors only | 35 | 41 | 44 | 0.780 |
| **In-memory BM25 and pgvector, vectors x3 (chosen)** | 34 | 43 | 47 | 0.778 |
| Postgres full-text and pgvector, vectors x3 | 31 | 42 | 45 | 0.739 |
| ParadeDB BM25 and pgvector, vectors x2 | 32 | 41 | 46 | 0.738 |
| Keywords alone: FTS5 / in-memory / ParadeDB / Postgres | 23 / 26 / 25 / 20 | 38 / 35 / 33 / 33 | 40 / 40 / 38 / 38 | 0.611 / 0.624 / 0.608 / 0.544 |

What this shows:

- Weighting vectors above keywords matters more than which database holds them.
- Keywords still earn their place: they lift top-6 hits from 44 to 47, for exact names that vectors miss.
- Postgres's built-in ranking is the weak part, because it is not BM25. ParadeDB adds BM25, but also an
  AGPL extension and a heavier image, for no gain here.
- pgvector returned the same results as an exact scan, in about 11 ms. Total ranking is about 16 ms,
  beside 40 to 80 ms for the query embedding.
- The in-memory index took 30 s to build in plain Python. That runs in the background after a sync,
  but it needs a faster build or an incremental one before larger catalogues.

**Context cost.** A search reply is about 2,900 characters (roughly 700 tokens) for 9 items, and a median
skill is 3,200 characters (800 tokens), so one find-and-load costs about 1,500 tokens. Better first-hit
ranking is what lets the default shrink. Next to test: 4 results instead of 6, and shorter descriptions.

**One embedder.** Every embedding goes through `POST /v1/embeddings`, which catalogue and memory
use as a normal OpenAI-compatible endpoint.

- **Primary and fallback.** Local Ollama first, with an optional hosted fallback for the same
  model. If the primary does not answer inside a budget, the fallback serves that request.
- **One model label.** Vectors are stored under a canonical name (`bge-m3`) whichever route produced
  them, with a table mapping that name to each provider's own (`baai/bge-m3`). Today, changing provider
  marks every vector stale, because the label is the provider's name.

If the embedder is down, search falls back to keyword and says so, as it does today. If it is
needed later, Qdrant can come back behind the same interface. Nothing else would change.

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

A general memory with namespaces, as an opt-in service, in the same Postgres with pgvector. It exposes an MCP server for agents and routes on the
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
| Data | Postgres dump (including vectors), tokens, `secrets.env` | Daily, incremental | Restoring it exactly |

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

## CLI container

Skills prefer CLIs to MCPs, and a CLI needs to exist somewhere. The `cli` service is an Alpine image
holding them, so nothing has to be installed on the host, and every deployment gets the same versions.
`canopy run gh repo list` runs a command inside it. Its home directory is `agent-data/cli`, so logins
survive an update and are backed up with the config tier.

Holds: `canopy`, `gh`, `aws`, `rclone`, `restic`, `jq`, `curl`, `pg_dump`, `git`, `uv` and `node`, with
`uvx` and `npx` for anything else. Tested on Alpine 3.22 on 2026-11-05: those all started, as did `vercel`,
`wrangler`, `hf` and `modal`, about 475 MB in all. That proves they start, not that each works: `wrangler`
needs a real command tried, because its runtime has had trouble on Alpine, and `uvx markitdown` resolved to
a very old version. `infisical` is not in Alpine's packages, so it needs its own install.

It has no Docker socket. Commands that manage the stack itself run on the host.

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

1. Folder layout, the `api` service, `/v1/status`, `canopy` CLI, `cli` image, gateway writes moved behind
   the API.
2. Contract suite in CI, with `compat.json`. Early, because every later step changes what it protects.
3. Postgres with pgvector, records and vectors moved there, embedder route, Ollama in `search`,
   weighted search, and the labelled queries as a regression check.
4. Scheduler, then backup and restore as jobs.
5. Storage profile, stage route and skill.
6. Connector plugin, manual and API reference.
7. Dashboard.
8. Memory.
9. Cache, if measurement still supports it.
10. Stable ids.

Enterprise mode stays the shipped default throughout.
