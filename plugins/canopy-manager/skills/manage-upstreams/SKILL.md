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

## Check before applying

`canopy validate` lists what is wrong with `servers.json`, and `canopy diff` says what `apply` would do: servers to register and remove, removals a group is blocking, groups to rebuild, and servers the config does not manage. Run both before `canopy apply`, and show the diff before anything is removed.

## Take an upstream out, or re-read it

- `canopy disable NAME` and `canopy enable NAME` keep it registered, so nothing has to be set up again. `canopy disable-tool` and `canopy enable-tool` do the same for one tool. This is the gateway's state: it is not in `servers.json`.
- `canopy refresh NAME` re-reads its tools, which the gateway otherwise freezes at registration. The reply lists what was added and removed. Its tools vanish for a moment, and what was disabled stays disabled. If it fails, the old definition is put back.
- `CANOPY_TOOL_REFRESH=true` does this daily for every upstream.

