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

3. Get the agent token, and connect Claude Code:

   ```bash
   TOKEN=$(clients/canopy token)
   claude mcp add --transport http canopy http://127.0.0.1:8080/v0/groups/core/mcp \
     --header "Authorization: Bearer $TOKEN"
   ```

4. In a new session, call `catalogue__health`. Expect `ok: true` and a `skill` count above zero.

The user's own content goes in `~/canopy/agent-data`, through the `manage-marketplaces` and
`manage-upstreams` skills. Settings and secrets go in `secrets.env` there.

Everything binds to 127.0.0.1. Before changing `CANOPY_BIND`, tell the user that the web UI has no
login of its own.
