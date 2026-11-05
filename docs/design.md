# Target design

Status: **proposed, 2026-11-05. Steps 1 to 3 of the order of work are built, step 3 with gaps it lists. Nothing else here is, unless it
says so.** For what runs today, see
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
| `seed` | One-shot: asks the API to apply the config | core | None |
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
- The prototype's in-memory index took 30 s to build in plain Python. The shipped one stems each distinct
  word once, and took 4.8 s on 5,162 synthetic records. It runs in the background after a sync.

**Context cost.** A search reply is about 2,900 characters (roughly 700 tokens) for 9 items, and a median
skill is 3,200 characters (800 tokens), so one find-and-load costs about 1,500 tokens. Better first-hit
ranking is what lets the default shrink. Next to test: 4 results instead of 6, and shorter descriptions.

**One embedder.** Every embedding goes through `POST /v1/embeddings`, which catalogue and memory
use as a normal OpenAI-compatible endpoint.

- **Provider is a setting.** `EMBED_PROVIDER` is `local` or `openrouter`, with `OPENROUTER_API_KEY` for
  the second. Unset means keyword search. A hosted provider is the sensible default for bulk and incremental runs, since a full
  index of this size costs cents. Ollama then matters for offline use and as a fallback.
- **Primary and fallback.** If the primary does not answer inside a budget, the fallback serves that
  request. This needs a model that both routes can serve, such as `bge-m3`.
- **One model label.** Vectors are stored under a canonical name (`bge-m3`) whichever route produced
  them, with a table mapping that name to each provider's own (`baai/bge-m3`). Today, changing provider
  marks every vector stale, because the label is the provider's name.
- **Model: `bge-m3`.** See the comparison below. Cloud and local vectors are interchangeable, so the
  fallback is safe and an index need not be re-embedded to change route. Measured on 2026-11-05, Ollama
  0.12.6 against OpenRouter `baai/bge-m3`: cosine 0.99999 on five short texts, one of them Hebrew, and
  0.99853 on one long text (cause not looked into). The largest change in any pairwise similarity was 0.003.

If the embedder is down, search falls back to keyword and says so, as it does today.

**Embedding models,** compared on 2026-11-05: 50 labelled queries against 1,050 records (the relevant ones
plus random distractors, so scores run higher than on the full index). One query is 2 points.

| Model | Dimensions | Cost per million tokens | First | Top 3 | Top 6 | MRR |
| --- | --- | --- | --- | --- | --- | --- |
| `bge-m3`, local | 1024 | Free locally, $0.01 hosted | 41 | 46 | 48 | 0.876 |
| `qwen3-embedding-8b` | 4096 | $0.01 | 43 | 46 | 46 | 0.889 |
| `voyage-4` | 1024 | $0.06 | 41 | 48 | 48 | 0.887 |
| `pplx-embed-v1-4b` | 2560 | $0.03 | 40 | 47 | 49 | 0.877 |
| `voyage-4-lite` | 1024 | $0.02 | 39 | 46 | 46 | 0.849 |
| `text-embedding-3-small` | 1536 | $0.02 | 34 | 43 | 45 | 0.786 |
| `cohere/embed-5-fast` | 2048 | $0.08 | 23 | 37 | 42 | 0.613 |

These are vector-only scores. The top four are within noise of each other, so `bge-m3` stays: it runs both
locally and hosted, is the smallest, and existing vectors carry over with no re-embedding. pgvector also
cannot index more than 2,000 dimensions of `vector`, which larger models would run into at scale.

**Reranking** is the step that pays. After fusion, the top 20 candidates go to a reranker with their
names and descriptions, and it returns the final order. Measured on the full index (50 queries):

| Step | First | Top 3 | Top 6 | MRR | Added time | Cost per search |
| --- | --- | --- | --- | --- | --- | --- |
| Fusion only | 34 | 43 | 47 | 0.782 | None | None |
| **`cohere/rerank-5-fast`, descriptions** | 43 | 46 | 48 | 0.896 | About 480 ms | $0.00004 |
| `cohere/rerank-5-fast`, with skill text | 39 | 47 | 48 | 0.864 | About 590 ms | $0.00016 |
| `qwen3-reranker-8b`, descriptions | 40 | 46 | 47 | 0.863 | About 550 ms | $0.0007 |
| `qwen3-reranker-8b`, with skill text | 38 | 47 | 47 | 0.850 | About 810 ms | $0.0018 |

Local rerankers were tested on the same queries, on 4 CPU threads (`fastembed`, ONNX). None is worth
shipping: `jina-reranker-v2-base-multilingual` reached 38 first and 45 in the top 3 at about 2.3 s per
search, `jina-reranker-v1-turbo-en` 35 and 42 at 440 ms, and `bge-reranker-base` made results worse. So
reranking is a hosted step. `bge-reranker-v2-m3` was not tested: it is larger, so slower still on CPU.

A far bigger gain than any embedding change, for about 4 cents per 1,000 searches. More text made it
worse: descriptions are the right input. It is a setting (`RERANK_MODEL`), off by default so a fresh install
needs no cloud account, and if it fails or times out, search returns the fused order. It sends the query
and candidate descriptions to the provider, so a user who wants nothing to leave the host leaves it off.

**Checked on the shipped code,** 2026-11-05: Canopy 4098852 from the published image, a real sync of 6
marketplaces (295 plugins, 3,884 records, no tool records as the test stack had no private upstreams),
and 3,848 vectors imported from an older index. 50 labelled queries, ranked among all kinds, limit 6.

| Mode | First | Top 3 | Top 6 | MRR | Median | p90 | Max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Keywords only | 24 | 35 | 40 | 0.599 | 13 ms | 30 ms | 43 ms |
| Fused, query embedded by local Ollama | 34 | 44 | 46 | 0.782 | 153 ms | 171 ms | 209 ms |
| Fused, query embedded by OpenRouter | 34 | 43 | 46 | 0.781 | 688 ms | 1,598 ms | 1,956 ms |
| Fused (OpenRouter) and `cohere/rerank-5-fast` | 43 | 47 | 48 | 0.900 | 1,198 ms | 1,772 ms | 2,884 ms |

- **The rewrite held up.** Fused and reranked land within one query of the tables above at every cut.
  Keywords alone are one query lower on first place (24 against 26). All of that is noise at this size.
- **The set is probably the same 50, but that is inferred.** The source set has 57 queries. 7 name only
  records that no longer exist, in the older index either, so they were left out rather than relabelled.
  The index differs too: smaller, and 3 plugins had left the marketplaces.
- **Scoring within the expected kind reads higher:** fused 38 / 44 / 46, 0.826; reranked 44 / 47 / 48, 0.910;
  keywords 27 / 38 / 40, 0.643. Ranking among all kinds is what matched the tables above.
- **The hosted query embedding is the slow part.** It costs about 530 ms at the median beside local, and far
  more at p90, for the same results: first place agreed on 50 of 50 queries and the top 3 on 49. The 40 to
  80 ms quoted above was a local figure. Rerank adds about 510 ms, as measured before.
- **`RERANK_TIMEOUT_SEC` at 2 s is about right:** one search in 50 fell back to the fused order in the scored
  pass. Raising it would rescue little and slow the worst case.
- **Cold start:** the first local embedding took 1.6 s, inside the 6 s query budget. Ollama used 1.5 GB.

## What the agent sees

The goal is the fewest tokens between a task and the right skill. In order of expected effect:

1. **Search has to happen at all.** An agent told to search first still often does not. So the connector
   adds a `UserPromptSubmit` hook: it searches on the user's prompt and, only on a confident match,
   injects up to 3 ids and one line each. Roughly 60 tokens when it fires and none when it does not.
   The signal is the best result's cosine similarity, not the fused score. On a real index of about
   4,800 records on 2026-11-05, the fused score was 3 to 4 for every prompt, "thanks that looks good"
   included: every query has a first place. Cosine with `bge-m3` at 0.60: 37 of 49 labelled queries
   fired, 27 with the right id shown, and 1 of 20 ordinary prompts. That is one small set, with
   negatives written by hand, so 0.60 is a starting value. Injection was proved in a real Claude Code
   session against a stand-in server.
2. **Descriptions are the real interface.** The agent picks from them, so a linter flags skills with
   weak ones, and the near-duplicates that split a query.
3. **An honest miss.** Replies carry `confidence`, and `weak: true` below `SEARCH_CONFIDENCE_MIN`, so a
   weak match reads as "nothing strong here" and the agent stops looking.
4. **A skill loads in layers.** `read` returns the skill. Long reference files are fetched only if the
   skill asks for them.
5. **Learning from use.** `system.search_log` records each query and what was then read. A search
   followed by no read is a likely miss, which tunes the labelled queries and shows up on the dashboard.

## Storage

[Garage](https://garagehq.deuxfleurs.fr), pinned at v2.5.0, in the `storage` profile. MinIO's community
edition is no longer an option: images stopped in October 2025 and the repo is archived.

Tested on 2026-11-05 against SeaweedFS 4.52, which also passed every functional test. Garage won on size:
6 MiB of RAM at idle against 72 MiB, and a 69 MB image against 780 MB, for what is a small staging
store. It runs as a separate unmodified container, so its licence (AGPL-3.0, to be confirmed) does not
touch Canopy's MIT.

- **Bootstrap.** The image has no shell, so the `api` service does the layout, buckets and keys
  through Garage's admin API, on first use. `api` also writes Garage's two secrets, so `storage` starts
  after it. `tests/contract/storage.sh` proves this on the real image, in CI.
- **Region.** Set `s3_region = "us-east-1"`, or presigned URLs from standard clients fail.
- **Host.** A presigned URL only works for the hostname it was signed with, on both stores tested. So
  `/v1/storage/stage` takes the audience (a stack service, or a remote client) and signs for that
  name.
- **Expiry.** A transient bucket has a lifecycle rule. Garage accepted the rule and returned it, but no
  deletion has been observed yet: its worker runs daily, so a test cannot wait for it. The `storage-expiry`
  job fails if an object outlives its rule by more than 2 days, and that failure path is tested.
- **Console.** Garage has none. The dashboard shows buckets and use.

## Memory

A general memory with namespaces, as an opt-in service, in the same Postgres with pgvector. It exposes an MCP server for agents and routes on the
API for operators. Tools: `remember`, `recall`, `list_memories`, `list_namespaces`, `update_memory`,
`forget`.

## Database

One Postgres instance, two databases, separate roles, created by the first start and upgraded by
numbered migrations. Agents never connect directly: they use the API.

| Database | Role | Holds |
| --- | --- | --- |
| `mcpjungle` | `mcpjungle` | The gateway's own tables |
| `canopy` | `canopy` | Everything below |

| Schema | Owner of the content | Tables |
| --- | --- | --- |
| `system` | Canopy, shipped and migrated | `sources` (repo URLs that ship with Canopy), `schema_version`, `jobs`, `job_runs`, `search_log`, `model_map` |
| `catalogue` | Canopy, rebuildable | `records` (text and vector), `plugins`, `reads` |
| `workspace` | The user | `sources` (their marketplaces), `namespaces`, `memories` |

A system source can be disabled but not edited, so an update can change it safely, and nothing the
user adds is touched by a migration. Marketplaces and namespaces are managed through the API, and the
`backup-config` job exports them to `agent-data/config` as JSON, so config stays readable and portable.

## Secrets

Canopy reads secrets from the environment, and `CANOPY_SECRETS` chooses how they get there: `env`, the
default, reads `secrets.env`. `infisical` fetches them at start, so nothing is written to disk. `op`
(1Password) works the same way. A provider is a small script that prints variables, so adding one
needs no change to Canopy. CLIs in the `cli` container are logged in on the server, so clients hold no credentials.

An optional `OPENROUTER_MANAGEMENT_KEY` lets the `manage` skill create and revoke scoped keys, so each
purpose (embeddings, reranking) has its own key and spending limit.

## Prior art

Checked on 2026-11-05, so that we adopt what exists. No project combines a private, self-hosted,
git-synced library at this scale with hybrid search, tools and a gateway.

| Project | What it is | Why it does not replace Canopy |
| --- | --- | --- |
| [claude-skills-mcp](https://github.com/K-Dense-AI/claude-skills-mcp) | Vector search over skills, staged loading | No longer maintained |
| [SkillsGate](https://github.com/skillsgate/skillsgate) | Public skill marketplace with semantic search | Installs public skills. Self-hosting not confirmed |
| agentskills search server | Queries the public skills.sh registry | Public registry, and installs by hand |
| Google ADK skill registry | `search_skills` and `load_skill` | Tied to ADK |
| Obot, LiteLLM, Archestra | Gateways with a skill registry | No skill search at this scale |

**Standards to adopt, not replace:**

- The Agent Skills format, unchanged.
- The MCP Skills extension ([SEP-2640](https://modelcontextprotocol.io/seps/2640-skills-extension)):
  `skills/list`, `skills/get` and `skill://` resources. The catalogue serves these, so any host that
  supports the extension can read Canopy's skills. The extension has no search, which is what Canopy adds.
  MCPJungle v0.4.7 answers `skills/list` with -32601, so they cannot go through the gateway: the API
  forwards them at `POST /v1/skills/mcp`, behind its token.
- Tool discovery is increasingly handled by clients: Claude Code defers tool definitions behind its own
  search. So the toolbox stays small and is not where effort goes. Skills are.

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
| `backup-config` | Hourly, pushing only on change | Push config only |
| `restore-test` | After `backup-data` | Restore the latest backup beside the stack and check it |
| `storage-expiry` | Daily | Confirm expiry ran on transient buckets |
| `update-check` | Daily | Report newer versions. Never applies them |
| `cli-update` | Weekly | Update added CLIs, after a smoke test |

A job takes a lock, so two runs never overlap. A missed run starts on the next boot.

Built so far: `sync`, `embed`, `health`, `storage-expiry`, `backup-data`, `backup-config`, and `restore-test`, which runs after
`backup-data`. The backup jobs start once `backup.json` exists. The rest of the table arrives with what each one does, and a job
named in `jobs.json` with nothing behind it is listed as not `runnable` and left alone. `every` takes a
number and m, h or d, and `after` chains a job to another's success. `embed` follows `EMBED_ON_SYNC`, off by
default, so a schedule never spends money by surprise. A sync no longer embeds, and the catalogue no longer
syncs by itself: without `api` nothing is scheduled. Read `canopy jobs`, or `GET /v1/jobs`.

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
| Config | `agent-data/config`, private skills and notes, `compose.override.yaml`, names of secrets but not their values | Hourly check, pushed on change | Rebuilding the install |
| Data | Postgres dump (including vectors), tokens, `secrets.env` | Daily, incremental | Restoring it exactly |

Both use [restic](https://restic.net) to any S3-compatible target, encrypted by default. `backup.json`
names the target. The catalogue and Ollama models are left out, because they rebuild themselves. A
backup is not trusted until a restore test has passed, and that test is itself a job.

Built, and `restic` (0.19.1) and `pg_dump` (client 17) are in the Canopy image, so the `backup` service and
`scripts/backup.sh` are gone. Details in [architecture](architecture.md#backup). `backup-config` checks hourly
rather than weekly because restic's `--skip-if-unchanged` makes "on change" one setting, and an unchanged
config has nothing to push. Proved on 2026-11-05 by `tests/contract/backup.sh`: a clean install, with its own
database passwords, backed up to a throwaway Garage v2.5.0, every folder destroyed, restored, and checked for
data, tokens, passwords and a further backup. That run found a bug: a restored database remembers git heads
whose clones are not in the backup, so sync skipped them and status stayed red for good. A missing clone now
counts as stale. Not yet run against a real target, or at the size of a real index.

## Updates

MCPJungle is the piece we do not control. Canopy depends on specific behaviour of it, listed in
[architecture](architecture.md#gateway-behaviour-the-seed-depends-on).

1. Every image is pinned. Nothing floats, and auto-updaters are off for Canopy's containers.
2. `compat.json` lists the MCPJungle versions Canopy has been tested against.
3. A scheduled CI job watches MCPJungle releases, starts the real stack on the new version, and runs
   the **contract suite**: one test per behaviour we rely on, including identities, group rebuilds,
   registration, tool listing and enterprise mode. A pass opens a PR that bumps the pin and `compat.json`
   (or an issue pointing at the branch, if the repo does not let Actions create PRs).
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

**Three tiers,** chosen by how often skills actually use each one. The counts are records mentioning the
CLI, out of 3,571 skills, commands and agents in a real catalogue on 2026-11-05.

| Tier | CLIs | How it gets there |
| --- | --- | --- |
| Baked into the image | `canopy`, `git`, `gh` (269), `curl` (202), `uv` (146), `jq` (101), `rclone` (87), `node` (82), `python3`, `aws`, `restic`, `pg_dump`, `ssh` | Image build |
| Media set, opt-in | `ffmpeg` (82), `ffprobe`, `imagemagick` (92), `exiftool` (29), `pdftotext`, `pandoc` | A second image tag, because these are large |
| Added per user | `hf` (37), `modal` (56), `wrangler`, `vercel`, `gcloud`, `op`, `infisical`, anything else | `canopy cli add <name>`, recorded in `agent-data/config/cli.json` |

Added CLIs install into `agent-data/cli` with `uv tool`, `npm` or a release download, so they survive an
image update. The library is `config/cli-library.json`. `canopy clis choose` is the picker, `canopy clis install` applies the
choices in `agent-data/config/cli.json`, and skill `manage-clis` is the agent's way in. It replaces the `canopy cli add`
planned above. Alpine packages are reinstalled after an update, because they live in the container. `uvx` and `npx` cover anything run only once.

**Updating them.** A `cli-update` job runs weekly. It compares each added CLI with its latest release,
installs the new version beside the old one, runs a smoke test (`--version`, and a read-only command if
`cli.json` names one), and switches only if that passes. Otherwise it keeps the old version and reports
it. `canopy cli status` and the dashboard show installed and latest versions, and `canopy cli update` runs
it on demand. A CLI can be pinned in `cli.json`. The baked tier updates with the image, through the normal
update path.

Tested on Alpine 3.22 on 2026-11-05: the baked CLIs, `vercel`, `wrangler`, `hf` and `modal` all started,
about 475 MB in all. That proves they start, not that each works. `wrangler` needs a real command tried,
because its runtime has had trouble on Alpine, and `uvx markitdown` resolved to a very old version.
`gcloud` is untested, and a large install. `infisical` needs a release download, not an Alpine package.

It has no Docker socket. Commands that manage the stack itself run on the host.

## Connecting a client

**Two plugins, for context cleanliness.** Whatever sits at user level costs context in every session, and
puts maintenance skills in front of an agent doing something else.

```
/plugin marketplace add danielrosehill/Canopy
/plugin install canopy-connector@canopy
```

- **`canopy-connector`** is the only one a user installs, at user level on every device. It holds the MCP
  connection to the user's own instance, one skill (`find`), the `/skill` command and the prompt hook.
- **`canopy-manager`** is for running the infrastructure: setup, diagnosis, upstreams, marketplaces, sync,
  backup, staging, CLIs. It is deployed on the server. While setting up, a user can add it at user level for
  a while. Its skills stay findable from any client, because the server indexes them.

Management needs the API token, so it works where the agent runs on the server, or where a token has been
given to that device.
`clients/canopy-pick`, the terminal picker for people, stays a separate add-on.

## Documentation

- `docs/manual.md`: the user manual. Install, connect, and above all **how finding skills works**,
  since that is the point of the system: what the connector skills do, how to search, how to load
  what you find, and how to call a long-tail tool.
- `docs/api.md`: every route, generated from the API.
- `docs/connect/`: a page per client (Claude Code, Codex, others). `saas-and-web-clients.md` covers ChatGPT,
  claude.ai and self-run web clients. Canopy documents those routes and ships none of them.

## Order of work

Each step ends with a clean install on an empty machine, and the contract suite once it exists.

1. **Done.** Folder layout, the `api` service, `/v1/status`, `canopy` CLI, `cli` image, gateway writes moved
   behind the API.
2. **Done.** Contract suite in CI, with `compat.json`, and the `MCPJungle watch` workflow. Early, because
   every later step changes what it protects.
3. **Done.** Postgres with pgvector, reranking step, roles, schemas and migrations, records and vectors moved
   there, embedder route, Ollama in `search`, weighted search, and a labelled query set as a regression check.
   `python -m canopy.import_vectors <file>` brings vectors over from an older SQLite index after a first full
   sync, so a move needs no full re-embed: on a real index it filled 3,848 of 3,884 records in 4 seconds, and
   the other 36 cost about 13,000 tokens. See "Checked on the shipped code".
4. **Done.** Scheduler, with `sync`, `embed` and `health` jobs, and backup and restore as jobs, proved on a clean
   install. The `backup` profile and its `/admin` mount are gone.
5. **Done.** Storage profile, stage route and skill, with the admin-API bootstrap proved on the real image.
6. **Done.** The `canopy-connector` and `canopy-manager` plugins, the prompt hook, MCP Skills extension,
   manual and API reference.
7. Dashboard.
8. Memory.
9. Cache, if measurement still supports it.
10. Stable ids.

Enterprise mode stays the shipped default throughout.
