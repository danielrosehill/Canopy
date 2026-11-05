---
name: manage-upstreams
description: Add or remove an MCP upstream or a tool group on a Canopy server. Use on "add this MCP", "remove this MCP server", or "change a group".
---

# Manage upstreams and groups

Upstreams and groups are in `agent-data/config/servers.json`. See `config/servers.example.json` in
the checkout for both forms. Secrets stay in `agent-data/secrets.env` and appear in the file as
`${VAR}`. A variable that is not set is refused, and the reply says which.

Add or remove one through the API. It checks the change by reading it back, then saves the config:

```bash
canopy add-server upstream.json        # add --replace to overwrite. - reads stdin
canopy remove-server NAME              # refused while a group names it
canopy set-group group.json            # builds the group
canopy remove-group NAME
canopy status
```

A new server does not join a group by itself: add it with `set-group`. Clients must reconnect to see a
changed tool list.

If you edit `servers.json` by hand, run `canopy apply`. It changes only what differs, and does not
delete servers, so an edit mistake cannot take one away. After a change to `secrets.env`, restart the
API first: `canopy compose up -d api`.

The `core` group is what agents list natively, and it always includes `catalogue` and `toolbox`.
Keep it small: everything else stays reachable through `toolbox__call_tool`.
