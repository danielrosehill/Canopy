# Canopy connector

The one Canopy plugin to install at user level. It stays small because it loads in every session:
the MCP connection, two small skills (`find`, and `test` to check the connection), the `/skill` command and a prompt hook.

```
/plugin marketplace add danielrosehill/Canopy
/plugin install canopy-connector@canopy
```

Set these in your environment:

| Variable | Value |
| --- | --- |
| `CANOPY_MCP_URL` | Your server's MCP address, such as `http://host:8080/v0/groups/core/mcp` |
| `CANOPY_TOKEN` | Your client token. Leave unset in development mode |
| `CANOPY_HOOK` | `off` turns off the prompt hook |

The hook searches on each prompt of three words or more, and names up to 3 skills only when the
server reports a confident match. Otherwise it prints nothing, and it never blocks a prompt.
