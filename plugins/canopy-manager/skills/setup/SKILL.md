---
name: setup
description: Install a Canopy server with Docker and connect Claude Code to it. Use on "set up Canopy", "install Canopy", or "connect to Canopy".
---

# Set up Canopy

Needs Docker with Compose v2.

1. Clone and start:

   ```bash
   git clone https://github.com/danielrosehill/Canopy.git && cd Canopy
   clients/canopy init
   clients/canopy up
   ```

2. `up` should end with `seed finished with 0 problem(s)`. Then check every part:

   ```bash
   clients/canopy status
   ```

3. Connect Claude Code. Have the user set these where their shell will keep them:

   ```bash
   export CANOPY_MCP_URL=http://127.0.0.1:8080/v0/groups/core/mcp
   export CANOPY_TOKEN=$(clients/canopy token)
   ```

   Then install the connector:

   ```
   /plugin marketplace add danielrosehill/Canopy
   /plugin install canopy-connector@canopy
   ```

4. **Validate, always.** Setup is not finished until this passes. In a new session, run the connector's `validate`
   skill. It checks the connection and `catalogue__health`, finds and loads a real tool and skill, and reports the
   latency and tokens. Expect `GREEN`. Give the user that report. On `RED`, use the `diagnose` skill.

5. Offer the CLAUDE.md snippet with the `claude-md-snippet` skill. It is optional, so write nothing without a yes.

The user's own content goes in `~/canopy/agent-data`, through the `manage-marketplaces` and
`manage-upstreams` skills. Settings and secrets go in `secrets.env` there.

Everything binds to 127.0.0.1. Before changing `CANOPY_BIND`, tell the user that the web UI has no
login of its own.
