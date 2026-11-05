# Keeping a private deployment separate

Run your own deployment from Canopy without putting your content in a checkout. Canopy is the
skeleton, and everything specific to you stays in an overlay directory that you keep elsewhere.

| Lives in Canopy | Lives in your overlay |
| --- | --- |
| `compose.yaml`, images, skills | `.env` with secrets and settings |
| Defaults in `config/` | `marketplaces.json`, `servers.json` |
| | Extra services, such as your own MCP servers |

```
my-canopy/
  .env
  config/marketplaces.json
  config/servers.json
  compose.override.yaml
```

Set `CANOPY_CONFIG_DIR` in that `.env` to the absolute path of `my-canopy/config`, then pass both
files to Compose:

```bash
docker compose --project-directory /path/to/Canopy \
  --env-file my-canopy/.env \
  -f /path/to/Canopy/compose.yaml -f my-canopy/compose.override.yaml up -d
```

Use the override file to add private upstreams as services on the `canopy` network, then list them in
`servers.json` by service name. They get no public image and never touch this repo.

## Settings that matter when moving an existing gateway

- `CANOPY_CLIENT_NAME` must match the name your client gives the server. If your skills say
  `mcp__my-gateway__<tool>`, set it to `my-gateway`.
- `CORE_GROUP` and `TOOLS_GROUP` can name your existing groups.
- `SERVER_MODE=development` keeps a gateway that has no auth. The seed then creates no identities.
- `EMBED_ON_SYNC=true` keeps embedding automatic.

Changing `EMBED_MODEL` invalidates stored vectors, because they are keyed by model name.
