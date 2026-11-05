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

Set `CANOPY_CONFIG_DIR` in that `.env` to the absolute path of `my-canopy/config`, and
`CANOPY_ENV_FILE` to the absolute path of the `.env` itself, then pass both files to Compose:

```bash
docker compose --project-directory /path/to/Canopy \
  --env-file my-canopy/.env \
  -f /path/to/Canopy/compose.yaml -f my-canopy/compose.override.yaml up -d
```

`--env-file` only fills in `compose.yaml`. The seed reads its own copy of the file to expand `${VAR}`
in `servers.json`, and without `CANOPY_ENV_FILE` it looks in the Canopy checkout and finds nothing.
If your secrets come from the process environment instead, list their names under the seed's
`environment` in the override file.

Use the override file to add private upstreams as services on the `canopy` network, then list them in
`servers.json` by service name. They get no public image and never touch this repo.

## Settings that matter when moving an existing gateway

- `CANOPY_CLIENT_NAME` must match the name your client gives the server. If your skills say
  `mcp__my-gateway__<tool>`, set it to `my-gateway`.
- `CORE_GROUP` and `TOOLS_GROUP` can name your existing groups.
- `SERVER_MODE=development` keeps a gateway that has no auth. The seed then creates no identities.
- `EMBED_ON_SYNC=true` keeps embedding automatic.

Changing `EMBED_MODEL` invalidates stored vectors, because they are keyed by model name.

## Moving a gateway that has no auth

If Canopy joins the network of an existing stack to reach its upstreams, check for name clashes.
A container on two networks can resolve a service name on either, so a second `db` on the old network
makes the gateway's `db` host ambiguous. Give Canopy's database an alias and set `DATABASE_URL` to it.

Change one thing at a time. Move with `SERVER_MODE=development` first, so that a failure can only come
from the move, and compare search results with the old stack. Then turn on enterprise mode as its own
step: hand a token to each client, and check every remote route. See the open question in
[deployment patterns](deployment-patterns.md#4-cloudflare-mcp-server-portal-for-hosted-clients).
