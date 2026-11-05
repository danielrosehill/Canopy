# Canopy

Self-hosted progressive discovery for AI agents, across skills and plugins as well as tools.

**Status: planning. Nothing is built yet, so there is nothing to install.**

## Why

Progressive discovery has been a thing for tools: an agent searches for a tool and loads only
what it needs. Skills and plugins have become the more useful surface, and they are where the
context problem has moved. Thousands of skills cannot all be installed or listed in a session.

Canopy targets that. Skills stay out of the agent's context until needed: the agent searches a
hybrid index and reads only what matches.

## What it will be

Canopy extends [MCPJungle](https://github.com/mcpjungle/MCPJungle), which it uses unmodified as
the MCP gateway. On top of that, one `docker compose up` will bring up:

- a catalogue that indexes the Claude Code plugin marketplaces you point it at
- a toolbox for calling long-tail tools without listing them all
- a skill picker for people: fuzzy search across the skills and tools on your server, in the
  terminal and in the web UI
- a management MCP and a web UI
- agent skills for common operations, such as adding a marketplace or triggering embeddings

Canopy ships with no content of its own. You bring your own marketplaces and MCP servers.
