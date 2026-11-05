# Canopy

Self-hosted progressive discovery for AI agents, across skills and plugins as well as tools.

**Status: early. The core stack, both pickers and the operations skills work. The management MCP
and stable ids are not built yet.**

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
- a **seed** service that sets up the gateway from your config, and is safe to re-run

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
docker compose up -d
docker compose logs -f seed     # wait for "seed finished with 0 problem(s)"
```

Connect Claude Code:

```bash
TOKEN=$(docker compose run --rm --no-deps -T seed python -m canopy.seed token)
claude mcp add --transport http canopy http://127.0.0.1:8080/v0/groups/core/mcp \
  --header "Authorization: Bearer $TOKEN"
```

Then ask your agent to search for a skill. A fresh server indexes only the Canopy plugin.

## Your own content

Copy `config/` somewhere outside the repo, and set `CANOPY_CONFIG_DIR` in `.env`:

- `marketplaces.json` lists the marketplaces to index. The catalogue reads it on every sync.
- `servers.json` lists MCP upstreams and groups, with `${VAR}` placeholders filled from `.env`. See
  `config/servers.example.json`. Run `docker compose up seed` after changing it.

All settings are described in `.env.example`.

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
| (none) | gateway, Postgres, catalogue, toolbox, seed | Yes |
| `ui` | Web UI | Yes |
| `backup` | Scheduled backup, with a checked restore path | Yes |
| `embeddings-local` | Ollama with bge-m3 | Not yet |
| `tunnel` | Cloudflare Tunnel | Not yet |
| `tailnet` | Tailscale sidecar | Not yet |

## Security

The gateway runs in MCPJungle's enterprise mode, so every request needs a bearer token, and all ports
bind to 127.0.0.1. The web UI has no login of its own, so put it behind something that does before
exposing it.

The default `canopy-agent` identity can reach every upstream through the toolbox. To narrow that, use
a gateway allow-list or `TOOLS_GROUP`. A marketplace you add is content your agents will read and
follow, so add only sources you trust.

## Design notes

- [Architecture](docs/architecture.md): how the stack fits together
- [Deployment patterns](docs/deployment-patterns.md): remote access with Tailscale and Cloudflare,
  including hosted clients such as ChatGPT and claude.ai
- [Private overlay](docs/private-overlay.md): keeping your own settings out of a Canopy checkout
- [MCP Deployment Architecture](https://github.com/danielrosehill/MCP-Deployment-Architecture): the
  conceptual notes behind this design

## Licence

MIT. MCPJungle is MPL-2.0 and runs as its unmodified upstream image.
