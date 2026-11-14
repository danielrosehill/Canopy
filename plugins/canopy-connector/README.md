# Canopy connector

The one Canopy plugin to install at user level. It stays small because it loads in every session:
the MCP connection, four small skills (`find`, `validate` to check and benchmark the connection, `recommended` to pick plugins from a checklist, and `migrate-context` to move your CLAUDE.md rules and notes into Canopy), the `/skill` command and a prompt hook.

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

Then, in a new session, ask your agent to validate Canopy, or run `/canopy-connector:validate`. It checks the
connection, finds and loads a real tool and skills, and reports the latency and tokens. Do this on every device.

The hook searches on each prompt of three words or more, and names up to 3 skills only when the
server reports a confident match. Otherwise it prints nothing, and it never blocks a prompt.

Ask for the recommended plugins, or run `/canopy-connector:recommended`, to choose from the
[Canopy Recommended](https://github.com/danielrosehill/Canopy-Recommended) marketplace and install your picks.
