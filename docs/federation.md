# Federated upstreams

Some MCP servers can serve many accounts at once, if something they trust tells them whose request each
one is. Canopy can be that something. Then one container serves every account, each account is its own
upstream on the gateway, and a group picks accounts by picking upstreams. Running one container per
account, the usual workaround, is not needed.

```
group voice_sam ──> gateway upstream google-workspace-sam ──┐   bearer CANOPY_SIGNER_TOKEN
group alex_core ──> gateway upstream google-workspace-work ──┼─> signer :8100 ──(ES256 assertion: email)──> google-workspace :8000
                    gateway upstream google-workspace-personal ┘     /jwks over loopback <──────────────── verifies it
```

The identity is enforced by the upstream, not by the model: a call made through `google-workspace-sam`
acts as Sam's account whatever arguments it carries.

## Google Workspace

Shipped as the `google-workspace` compose profile, on
[google_workspace_mcp](https://github.com/taylorwilsdon/google_workspace_mcp) in its trusted-gateway mode.
Each account's Google sign-in is one file in `$CANOPY_DATA/google-workspace/`, named by email, and it keeps
the OAuth client it was issued under, so accounts from different OAuth clients can share the server.

1. In `config/servers.json`, one entry with a profile per account. Identities can come from the
   environment, as anything else in the file can:

   ```json
   {"name": "google-workspace", "description": "Google Workspace: Gmail, Calendar, Drive, Docs and more",
    "profiles": {"personal": "${GWS_PERSONAL}", "work": "${GWS_WORK}", "sam": "${GWS_SAM}"},
    "new_tools": "disabled"}
   ```

   The gateway gets `google-workspace-personal`, `google-workspace-work` and `google-workspace-sam`.
   Name those in groups as any other server.
2. In `secrets.env`: `CANOPY_SIGNER_TOKEN` (any long random string), `GOOGLE_OAUTH_CLIENT_ID` and
   `GOOGLE_OAUTH_CLIENT_SECRET` (a Google Cloud OAuth client of type Desktop or Web with redirect
   `http://localhost:8000/oauth2callback`), and the `GWS_*` identities. Add `google-workspace` to
   `COMPOSE_PROFILES`, then `canopy up` and `canopy apply`.
3. Sign each account in once. The callback goes to the server's port, published on the host's loopback
   only (`CANOPY_GWS_OAUTH_PORT`, default 8000), so from the computer with the browser:

   ```bash
   ssh -N -L 8000:127.0.0.1:8000 canopy-host        # skip on the host itself
   canopy compose exec gateway /mcpjungle invoke google-workspace-sam__start_google_auth \
     --input '{"service_name": "Google Calendar"}'
   ```

   Open the URL it returns, sign in as that account (another account is refused), and approve.

An OAuth client of type Internal admits only accounts of its own Workspace organisation. An External
client in Testing mode issues tokens that die after 7 days: publish it for a household or personal use.

To use your own build of the server, set `GOOGLE_WORKSPACE_IMAGE`.

## Another server

Any upstream that accepts a signed identity from a gateway can be federated. Give its entry `profiles`,
run it with the signer beside it (as the `google-workspace` services in `compose.yaml` do, with
`SIGNER_FOR` naming the entry), and set what it expects in a `signer` block:

| Key | Default | What |
|---|---|---|
| `upstream` | `http://127.0.0.1:8000/mcp` | where the signer forwards to |
| `header` | `x-pomerium-jwt-assertion` | the header carrying the assertion |
| `claim` | `email` | the claim holding the identity |
| `audience` | the entry's name | the `aud` claim |
| `ttl` | `60` | seconds an assertion lives |

The signer makes a new key at each start and publishes it at `/jwks`. It accepts only requests that carry
`CANOPY_SIGNER_TOKEN`, drops any assertion a caller supplied, and never passes the token on.

## Status

The design was piloted on 2026-11-20 against google_workspace_mcp 2.1 with two accounts from different
OAuth clients: each profile acted as its own account, a call naming the other account's email still acted
as the profile's own, and a request with no assertion was refused. The signer and the config expansion
have unit tests. A sign-in of a new account through a profile has not been run yet.
