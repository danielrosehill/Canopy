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
| A secret or a setting | `secrets.env` | `canopy up` |
| Command-line tools for agents | `config/cli.json`, through `canopy clis` | `canopy clis install` |

## The formula

1. **Make the folder a private repo.** Copy `examples/overlay/` to a new private git repo, and copy
   `secrets.env.example` to `secrets.env`. Its `.gitignore` keeps `secrets.env` out of git.
2. **Point Canopy at it,** with `export CANOPY_AGENT_DATA=~/my-canopy` in your shell profile. `canopy up`
   and `canopy compose ...` then pick up everything in it, including the override file.
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
[`examples/ops-notebook/`](../examples/ops-notebook) is the skeleton, and the `canopy-manager` skill `ops-log`
sets one up, writes entries and searches them.
