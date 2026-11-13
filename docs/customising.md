# Add your own things

Canopy is the public skeleton. Your skills, MCP servers, secrets and settings live in one folder of your own,
`CANOPY_AGENT_DATA`, so you never edit a Canopy checkout, and an update never touches your work.
[`examples/overlay/`](../examples/overlay) is a working copy of that folder to start from.

| You want to add | Put it in | Then run |
| --- | --- | --- |
| Skills, private or public | A plugin marketplace repo, listed in `config/marketplaces.json` | `canopy jobs run sync` |
| A hosted MCP server | `config/servers.json` | `canopy apply` |
| Your own MCP server | A service in `compose.override.yaml`, and `config/servers.json` | `canopy up`, then `canopy apply` |
| Which tools agents see directly | `groups` in `config/servers.json` | `canopy apply` |
| A setting that is not secret | `settings.env`, committed | `canopy up` |
| A secret | `secrets.env`, never committed, or a secrets manager through `env.sh` | `canopy up` |
| Command-line tools for agents | `config/cli.json`, through `canopy clis` | `canopy clis install` |

## The formula

1. **Make the folder a private repo.** Copy `examples/overlay/` to a new private git repo, and copy
   `settings.env.example` to `settings.env`. Secrets go in `secrets.env`, which its `.gitignore` keeps out
   of git, or come from a secrets manager through `env.sh`: see [Settings and secrets](#settings-and-secrets).
2. **Point Canopy at it,** with `export CANOPY_AGENT_DATA=~/my-canopy` in your shell profile. `canopy up`
   and `canopy compose ...` then pick up everything in it, including the override file. Or point
   `CANOPY_OVERLAY` at the repo's URL, and Canopy keeps its own clone: see
   [Overlay by git URL](#overlay-by-git-url).
3. **Add your skills.** Put them in a plugin marketplace repo, which can be private, and add it to
   `config/marketplaces.json` as `{ "name": "...", "repo": "owner/repo" }`. A private repo needs
   `GITHUB_TOKEN` in `secrets.env`. `canopy compose up -d catalogue` loads a new token, and
   `canopy jobs run sync` indexes the repo.
4. **Add your MCP servers.** A private one is a service on the `canopy` network in
   `compose.override.yaml`, and `config/servers.json` reaches it by service name, as
   `http://my-mcp:8000/mcp`. It needs no public image and no published port. A hosted one is only an entry in
   `servers.json`.
5. **Keep secrets out of the JSON.** Write `${MY_TOKEN}` in `servers.json` and the value in `secrets.env`,
   and the API expands it. If your secrets come from the process environment instead, as with a secrets
   manager, list their names with no value under the `api` service's `environment` in the override file.
6. **Choose what agents see.** Tools in a group are listed to agents directly. Everything else stays
   searchable and is called through the toolbox, which costs far less context. Keep the group small.
7. **Apply and check,** with `canopy apply` and `canopy status`. Then search for one of your skills
   through the `canopy-connector` plugin's `validate` skill.

Commit the folder after each change: it is your whole deployment, apart from the data, and it is what you
restore from. For anything `canopy` should run beyond this, wrap it in a script in the same repo.

## Settings and secrets

Three sources, read in this order. A later one wins over an earlier one.

| File | Holds | In git |
| --- | --- | --- |
| `settings.env` | Settings that are not secret: `CANOPY_TAG`, `CANOPY_CLIENT_NAME`, `CORE_GROUP`, ports and binds, `EMBED_*`, `SEARCH_*` | Yes |
| `secrets.env` | Secrets: `GITHUB_TOKEN`, API keys, `RESTIC_PASSWORD`, the values behind `${VAR}` in `servers.json` | Never |
| `env.sh` | A script that prints secrets from a secrets manager | Yes: it holds no values |

All three reach compose's `${VAR}` substitution. Only `secrets.env` and `env.sh`'s output also reach the
`api` and `cli` containers, so the API can expand `${VAR}` in `servers.json`. `settings.env` stays out of the
containers on purpose: some names mean one thing to compose and another inside, such as `CANOPY_API_PORT` (the
published port outside, the listen port inside). Every setting a container needs is passed by name in
`compose.yaml`. A non-secret value that `servers.json` uses goes in `secrets.env` or comes from `env.sh`.
`canopy` itself also reads `settings.env` for its own settings, such as `CANOPY_API_PORT` and `CANOPY_API_BIND`,
after your shell and the checkout's `.env`. The names that locate the folders (`CANOPY_OVERLAY`, `CANOPY_DATA`,
`CANOPY_AGENT_DATA`) come only from the shell or the checkout's `.env`. A variable exported in your shell wins over both files, and `env.sh`
wins over everything. `.env.example` describes every setting.

### The `env.sh` hook

If the folder has an executable `env.sh`, `canopy` runs it before any `docker compose` call, in that
folder, with your environment plus `settings.env` and `secrets.env`. It prints `KEY=VALUE` lines on
stdout. Blank lines and lines starting with `#` are skipped, `export ` is allowed, and one pair of quotes
around a value is removed. Anything else stops `canopy` with the number of the bad line, never its value.

The values stay in memory. `canopy` writes only their names, to `$CANOPY_DATA/agent-state/hook-env.yaml`,
so compose passes each into `api` and `cli` from its own environment. If the script fails, `canopy` stops
with its exit code and shows its stderr. Its stdout is never shown.

`canopy --no-hooks ...`, or `CANOPY_NO_HOOKS=1`, skips the hook while you debug.

The hook needs one credential of its own, such as a machine identity's secret. Keep it in your shell
environment or in `secrets.env` on that machine, never in git.

Infisical, with a machine identity that uses universal auth:

```sh
#!/bin/sh
set -eu
# INFISICAL_CLIENT_ID and INFISICAL_PROJECT_ID can be in settings.env.
# INFISICAL_CLIENT_SECRET stays local: your shell environment, or secrets.env.
INFISICAL_TOKEN=$(infisical login --method=universal-auth --client-id="$INFISICAL_CLIENT_ID" \
  --client-secret="$INFISICAL_CLIENT_SECRET" --silent --plain)
export INFISICAL_TOKEN
infisical export --projectId="$INFISICAL_PROJECT_ID" --env=prod --format=dotenv
```

1Password, with a service account (`OP_SERVICE_ACCOUNT_TOKEN` stays local). `secrets.tpl` is committed,
and holds references, not values, as `MY_TOKEN={{ op://my-vault/my-item/credential }}`:

```sh
#!/bin/sh
set -eu
op inject --in-file secrets.tpl
```

`op run --env-file secrets.tpl -- sh -c 'printf "MY_TOKEN=%s\n" "$MY_TOKEN"'` works too, if you would
rather name each variable. Check that the tool prints nothing else on stdout, or the hook fails.

## Overlay by git URL

Instead of a folder you keep in sync yourself, give Canopy the URL of your overlay repo:

```bash
export CANOPY_OVERLAY=git@github.com:you/my-canopy.git     # or https://github.com/you/my-canopy.git
export CANOPY_OVERLAY_REF=main                             # optional: a branch, tag or commit
```

`canopy` keeps a clone in `$CANOPY_DATA/overlay`. `canopy up`, `canopy apply`, `canopy compose ...`,
`canopy init` and `canopy overlay pull` clone it if it is missing, or fetch it, then check out the ref:
a branch is fast-forwarded, a tag or commit is checked out detached. With no ref it follows the remote's
default branch. `canopy overlay status` shows the URL, ref, commit, whether the clone is clean, and how
far it is behind or ahead, as of the last fetch.

Never edit the clone. To change something, commit to the overlay repo and run `canopy apply` (or
`canopy up` for compose changes). A clone with local changes or local commits is not touched: `canopy`
stops and says so. Delete the folder to clone it afresh.

With `CANOPY_OVERLAY` set, the clone replaces `CANOPY_AGENT_DATA` as the source of `config/`,
`compose.override.yaml`, `settings.env`, `env.sh`, `skills/` and `notes/`. If both are set, `canopy` says
once that `CANOPY_AGENT_DATA` is not used. Unset, nothing changes.

**Access.** The one thing the machine needs is read access to the repo. Either git already has it (an
SSH key or deploy key, or a credential helper), or, for an `https://github.com/` URL, `GITHUB_TOKEN` is in
the environment or in the local `secrets.env`. The token is passed to that one git call through its
environment. It is not written to disk or to the clone's `.git/config`, and it is never printed. It is
sent to `github.com` only: use a credential helper for other hosts.

**What lives where.** The clone is git's, so containers get it read-only. What they write goes to
`$CANOPY_DATA/agent-state`:

| Mount | Without an overlay | With an overlay |
| --- | --- | --- |
| `/config` in `catalogue` | `agent-data/config`, read-only | the clone's `config/`, read-only |
| `/config` in `api` | `agent-data/config`, read-write | the clone's `config/`, read-only |
| `/agent-data` in `api` (backups read it) | `agent-data`, read-write | the clone, read-only |
| `/agent-data` in `cli` | `agent-data`, read-write | the clone, read-only |
| `/agent-data/cli` in `cli` (its home: CLI logins) | inside the above | `agent-state/cli`, read-write |
| `/agent-state` in `api` (backups: the export and `secrets.env`) | not mounted | `agent-state`, read-write |
| `secrets.env` | `agent-data/secrets.env` | `agent-state/secrets.env`, optional |

`compose.overlay.yaml` in the checkout holds that mapping, and `canopy` adds it only with an overlay.

Because the config is read-only:

- `add-server`, `remove-server`, `set-group`, `remove-group`, `add-marketplace` and
  `remove-marketplace` are refused by `canopy`, and fail in the API.
- `canopy clis choose`, `add` and `remove` edit `config/cli.json` in the clone, so the next pull stops.
  Edit `cli.json` in the overlay repo instead. `canopy clis install` and `logins` work.

Backups still cover everything. The `api` container sets `CANOPY_CONFIG_EXPORT=/agent-state/export.json`
and `CANOPY_AGENT_STATE_DIR=/agent-state`, so the `backup-config` job writes its export there and backs it
up with the clone's config, and `backup-data` includes `agent-state/secrets.env` as it includes
`agent-data/secrets.env`. `canopy restore` puts those two back in `agent-state`, and the backed-up copy of
the clone in `$CANOPY_DATA/agent-state/restored`, for reference: your config comes from the overlay.

## Updating

```bash
canopy update
```

This moves to the newest release `CANOPY_TAG` allows. It pulls first, so the only downtime is the swap, then
checks `canopy status`, and goes back to the previous images if the new ones are unhealthy. Your folder and
data are not touched. Going back cannot undo a database migration, so read a release's notes if it has one.

| `CANOPY_TAG` | Follows |
| --- | --- |
| `v1` (default) | Every `v1` release. A breaking change becomes `v2`, so this never takes one by surprise |
| `v1.00` | Stays on that release |
| `main` | Every commit that passes CI |
| A commit hash | Stays on that commit: the strictest pin, for a deployment you test before moving |

`canopy update` puts your Canopy checkout on the matching commit, detached, and stops if the checkout has
local changes. A release is the exact image CI tested, under another name: pushing a tag runs
`.github/workflows/release.yml`, so tag a commit only after CI has passed on it.

Images are on [Docker Hub](https://hub.docker.com/r/danielrosehill/canopy), with the same tags on GHCR
(`ghcr.io/danielrosehill/canopy`) as a mirror. To use the mirror, add the image to your override file.

## Settings that matter when moving an existing gateway

- `CANOPY_CLIENT_NAME` must match the name your client gives the server. If your skills say
  `mcp__my-gateway__<tool>`, set it to `my-gateway`.
- `CORE_GROUP` and `TOOLS_GROUP` can name your existing groups.
- `SERVER_MODE=development` keeps a gateway that has no auth. No identities are created, but the
  control API still needs its token.
- `EMBED_ON_SYNC=true` keeps embedding automatic.

Changing `EMBED_MODEL` invalidates stored vectors, because they are keyed by model name.

## Moving a gateway that has no auth

If Canopy joins the network of an existing stack to reach its upstreams, check for name clashes.
A container on two networks can resolve a service name on either, so a second `db` on the old network
makes the gateway's `db` host ambiguous. Give Canopy's database an alias and set `DATABASE_URL` to it.

Change one thing at a time. Move with `SERVER_MODE=development` first, so that a failure can only come
from the move, and compare search results with the old stack. Then turn on enterprise mode as its own
step: hand a token to each client, and check every remote route. See the open question in
[deployment patterns](deployment-patterns.md#4-cloudflare-mcp-server-portal-for-hosted-clients).

## A scoped catalogue for a second user

The catalogue indexes every configured marketplace. To give another person catalogue search without
showing them the rest, run a second catalogue container from the same image and database with:

- `CATALOGUE_MARKETPLACES=<name>,<name>`: plugins, skills, commands and agents are visible only if their
  plugin comes from one of these marketplaces.
- `CATALOGUE_SERVERS=<server>,<server>`: tool and mcp records are visible only for these gateway servers.
  Match it to the group that person's toolbox calls into.

Either variable turns scoping on; anything unlisted is absent from `search`, `list` and `read` (a read
answers "no such record", the same as for an id that does not exist, and side files follow their skill
or plugin). A scoped instance serves only those three tools: `health`, `usage`, `sync`, `embed` and
`mcp_servers` describe or act on the whole index and are not registered. Tool hits always give
`toolbox__call_tool` as the route, since "listed natively" refers to the main client group. Register the
container as its own upstream and put it in that person's group only. Shared index, so no extra sync.

## Ops notebook

Keep a log of what you change: updates, maintenance, incidents and decisions, across the stack and the
systems around it. It is a separate **private** git repo of markdown (it will name your systems, so it can't
live in a public fork), which also opens as an Obsidian vault.
The public template [`Ops-Notebook-Template`](https://github.com/danielrosehill/Ops-Notebook-Template) is the
skeleton. The `canopy-manager` skill `ops-notebook-setup` creates your private copy and tailors its areas and
sections to your setup; `ops-log` writes entries and searches them.
