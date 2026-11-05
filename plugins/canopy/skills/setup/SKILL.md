---
name: setup
description: Install a Canopy server with Docker and connect Claude Code to it. Use on "set up Canopy", "install Canopy", or "connect to Canopy".
---

# Set up Canopy

Needs Docker with Compose v2.

1. Clone and start:

   ```bash
   git clone https://github.com/danielrosehill/Canopy.git && cd Canopy
   docker compose up -d
   ```

2. Wait for the seed to finish. It should end with `seed finished with 0 problem(s)`:

   ```bash
   docker compose logs -f seed
   ```

3. Get the agent token, and connect Claude Code:

   ```bash
   TOKEN=$(docker compose run --rm --no-deps -T seed python -m canopy.seed token)
   claude mcp add --transport http canopy http://127.0.0.1:8080/v0/groups/core/mcp \
     --header "Authorization: Bearer $TOKEN"
   ```

4. In a new session, call `catalogue__health`. Expect `ok: true` and a `skill` count above zero.

For the user's own content, tell them to copy `config/` outside the repo, set `CANOPY_CONFIG_DIR` in
`.env`, then use the `manage-marketplaces` and `manage-upstreams` skills.

Everything binds to 127.0.0.1. Before changing `CANOPY_BIND`, tell the user that the web UI has no
login of its own.
