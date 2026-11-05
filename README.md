# Canopy

Self-hosted progressive discovery for AI agents, across skills and plugins as well as tools.

**Status: early. The core stack, control API, CLI, both pickers and the operations skills work. The
rest of the [target design](docs/design.md) is not built yet.**

## Why

Progressive discovery has been a thing for tools: an agent searches for a tool and loads only
what it needs. Skills and plugins have become the more useful surface, and they are where the
context problem has moved. Thousands of skills cannot all be installed or listed in a session.

Canopy targets that. Skills stay out of the agent's context until needed: the agent searches a
hybrid index and reads only what matches.

## What you get

Canopy extends [MCPJungle](https://github.com/mcpjungle/MCPJungle), which it uses unmodified as the
MCP gateway. On top of that, `docker compose up -d` brings up:

- a **catalogue** that indexes the Claude Code plugin marketplaces you point it at, with keyword
  search out of the box and hybrid search if you add an embeddings endpoint
- a **toolbox** for calling long-tail tools without listing them all
- a **control API** and a `canopy` command for managing all of it, the only place that holds the
  gateway's admin token
- a **seed** service that applies your config through the API, and is safe to re-run
- a **cli** container with `gh`, `aws`, `rclone`, `restic` and others, so skills can use them without
  installing anything on the host

An agent connects to one small group of 12 tools. Skills and other tools stay out of its context
until it searches for them.

For people, there is a **skill picker**: fuzzy search across the skills and tools on your server, in
the terminal (`clients/canopy-pick`) and in a web UI (the `ui` profile).

Setting up and running Canopy has its own public Claude Code marketplace, published from this
repo. Its agent skills cover installing the stack and common operations, such as adding a
marketplace or triggering embeddings.

Apart from those operations skills, Canopy ships with no content of its own. You bring your own
marketplaces and MCP servers.

## Quick start

Needs Docker with Compose v2.

```bash
git clone https://github.com/danielrosehill/Canopy.git && cd Canopy
clients/canopy init             # makes ~/canopy/agent-data and ~/canopy/data
clients/canopy up               # ends with "seed finished with 0 problem(s)"
clients/canopy status
```

Link `clients/canopy` into your `PATH` to drop the prefix.

Connect Claude Code:

```bash
TOKEN=$(clients/canopy token)
claude mcp add --transport http canopy http://127.0.0.1:8080/v0/groups/core/mcp \
  --header "Authorization: Bearer $TOKEN"
```

Then ask your agent to search for a skill. A fresh server indexes only the Canopy plugin.

## Your own content

Two folders, kept outside the checkout. `CANOPY_AGENT_DATA` is yours, and `CANOPY_DATA` is Canopy's:

```
~/canopy/agent-data/   config/  skills/  notes/  cli/  secrets.env
~/canopy/data/         postgres/  catalogue/  api/  admin/  tokens/  backups/
```

In `agent-data/config`:

- `marketplaces.json` lists the marketplaces to index. The catalogue reads it on every sync.
- `servers.json` lists MCP upstreams and groups, with `${VAR}` placeholders filled from `secrets.env`.
  See `config/servers.example.json`. Run `canopy apply` after changing it.

All settings are described in `.env.example`.

## Manage it

The API listens on `127.0.0.1:8090`, and `canopy` is a client of it:

```bash
canopy status                       # every component in one reply
canopy servers                      # also groups, tools
canopy add-server upstream.json     # registers it, confirms by reading back, saves it to config
canopy remove-server NAME
canopy run gh repo list             # any command, in the cli container
canopy api GET /v1/gateway/groups   # any route
```

## Find skills yourself

In the terminal, with only Python and no other dependencies:

```bash
export CANOPY_TOKEN=$TOKEN
clients/canopy-pick                       # type to filter, Enter to pick and copy the id
clients/canopy-pick --semantic "send an invoice"
clients/canopy-pick --kind tool --json dns
```

In the browser, at <http://127.0.0.1:8081>:

```bash
docker compose --profile ui up -d
```

Then hand the id to your agent with `/skill <id>` from the Canopy plugin:

```
/plugin marketplace add danielrosehill/Canopy
/plugin install canopy@canopy
```

## Profiles

| Profile | Adds | Tested |
| --- | --- | --- |
| (none) | gateway, Postgres, catalogue, toolbox, API, seed, cli | Yes |
| `ui` | Web UI | Yes |
| `backup` | Scheduled backup, with a checked restore path | Yes |
| `search` | Ollama with bge-m3, with `EMBED_PROVIDER=local` | Not yet |
| `tunnel` | Cloudflare Tunnel | Not yet |
| `tailnet` | Tailscale sidecar | Not yet |

## Security

The gateway runs in MCPJungle's enterprise mode, so every request needs a bearer token, and all ports
bind to 127.0.0.1. The API has its own token, needed in every mode. Anything run with `canopy run` can
read that token, so treat the `cli` container as an operator's shell. The web UI has no login of its own, so put it behind something that does before
exposing it.

The default `canopy-agent` identity can reach every upstream through the toolbox. To narrow that, use
a gateway allow-list or `TOOLS_GROUP`. A marketplace you add is content your agents will read and
follow, so add only sources you trust.

## Design notes

- [Architecture](docs/architecture.md): how the stack fits together
- [Deployment patterns](docs/deployment-patterns.md): remote access with Tailscale and Cloudflare,
  including hosted clients such as ChatGPT and claude.ai
- [Target design](docs/design.md): where this is heading, and the order of work
- [Private overlay](docs/private-overlay.md): keeping your own settings out of a Canopy checkout
- [MCP Deployment Architecture](https://github.com/danielrosehill/MCP-Deployment-Architecture): the
  conceptual notes behind this design

## Licence

MIT. MCPJungle is MPL-2.0 and runs as its unmodified upstream image.
