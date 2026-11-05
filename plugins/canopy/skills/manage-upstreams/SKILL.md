---
name: manage-upstreams
description: Add or remove an MCP upstream or a tool group on a Canopy server. Use on "add this MCP", "remove this MCP server", or "change a group".
---

# Manage upstreams and groups

Upstreams and groups are in `servers.json`, in `CANOPY_CONFIG_DIR` (default `./config`). See
`config/servers.example.json` for both forms. Secrets stay in `.env` and appear in the file as
`${VAR}`. A variable that is not set fails that server, and says so.

After any edit, run the seed. It changes only what differs:

```bash
docker compose up seed
```

Check that it ends with `seed finished with 0 problem(s)`. It rebuilds groups when a server is
added, because a new server does not join an existing group by itself. Clients must reconnect to see
a changed tool list.

To remove an upstream, delete it from `servers.json`, then deregister it. The seed does not delete
servers, so that an edit mistake cannot take one away:

```bash
ADMIN=$(docker compose run --rm --no-deps -T seed cat /admin/admin)
curl -X DELETE -H "Authorization: Bearer $ADMIN" http://127.0.0.1:8080/api/v0/servers/<name>
```

The `core` group is what agents list natively, and it always includes `catalogue` and `toolbox`.
Keep it small: everything else stays reachable through `toolbox__call_tool`.
