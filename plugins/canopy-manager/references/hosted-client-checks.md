# Hosted client checks: the server side

Shared by `debug-chatgpt` and `debug-claude-ai`. A hosted client (ChatGPT, claude.ai) runs in its vendor's cloud and
reaches Canopy through an OAuth ingress, usually a Cloudflare MCP server portal
([pattern 4](../../../docs/deployment-patterns.md#4-cloudflare-mcp-server-portal-for-hosted-clients)), in front of the
front door (`front`, [hosted clients](../../../docs/connect/saas-and-web-clients.md)) or a gateway group.

```
client --OAuth--> ingress (portal + access policy) --tunnel--> front door :8070/mcp --hub--> gateway group --> upstreams
                                                                     \--> catalogue /scoped (search, read, skills)
```

Work from the right of the diagram to the left: prove each hop answers before blaming the one before it.

## 1. Did the client call at all?

Note the time of the failing chat (UTC), then read the logs around it:

```bash
canopy compose logs --since 30m front | grep -v 127.0.0.1     # front door: every client request
canopy compose logs --since 30m gateway | grep '/v0/groups/'   # gateway groups, if the ingress points at one
```

- **No request at the chat's time:** the client never called. The fault is in the client (wrong app or connector in
  that chat, stale tool list, no consent for that server). Go to the client skill's steps; the server is fine.
- **A burst of `initialize` / `tools/list` and nothing else:** that is the ingress syncing or a client connecting,
  not a tool call. A tool call is a `POST /mcp` that takes noticeably longer (a search is ~1 s, a print several).
- **401 on the front door:** the ingress sends a token no profile owns. Adding `spaces.json` retires the `default`
  profile and its token: each profile has `data/front-tokens/profile-<name>.token`. Update the ingress credential.

## 2. Does the front door answer, as that profile?

Run on the server, with the profile's token. The client sees exactly this.

```bash
T=$(sudo cat "$CANOPY_DATA/front-tokens/profile-<name>.token")
call() { curl -s -m 90 http://<front-bind>:8070/mcp -H "Authorization: Bearer $T" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$1"; echo; }
call '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'                       # expect the five tools
call '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search","arguments":{"query":"<the task>"}}}'
call '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_servers","arguments":{}}}'
call '{"jsonrpc":"2.0","id":4,"method":"skills/list","params":{}}'          # SEP-2640
```

| Symptom | Cause and fix |
| --- | --- |
| `search` finds the skill but not the tool | The catalogue indexes tools from `TOOLS_GROUP`. Set it on the `catalogue` service to the group the front door's hub uses, then `canopy sync --gateway`. |
| `list_servers` lacks a server the group has | The hub listed tools before the group changed. Wait `FRONT_REFRESH_SEC` (default 300 s) or `canopy compose restart front`. |
| `call_tool` says `no tool` | Out of the profile's spaces (`spaces.json`), or absent from the hub. Out of scope and absent look the same on purpose. |
| `call_tool` says `not connected` | The gateway group is down: `canopy compose logs gateway`. |
| `search` errors with `scoped by its environment` | The front door points at an env-scoped catalogue (`CATALOGUE_MARKETPLACES`); point it at the main one and scope with profiles. |

## 3. Is the ingress healthy?

For a Cloudflare portal (`/accounts/<id>/access/ai-controls/mcp/...`, token with MCP Portals read):

- `GET servers/<id>`: `status` must be `ready`, `tools` the expected count (five for a front door), `error` empty.
  `POST servers/<id>/sync` re-reads it. A server that is not `ready` is never offered to clients.
- The server must be in the portal: `GET portals/<id>` lists `servers[].server_id`. A `PUT` of the portal replaces
  the whole list, so always send every existing server.
- Each server needs an Access application of type `mcp` with a policy that admits the person, or the consent page
  says "No allowed servers available".
- Upstream auth: `auth_type: bearer` with the profile's token as `auth_credentials` for a front door. It is
  write-only: the API never returns it, so a 401 in the front door's log is how you see a wrong one.

## 4. Record it

Write what was wrong and what fixed it where the deployment keeps its notes (an ops log, a docs page). A hosted
client failure is usually one of four things: wrong connector in the chat, stale tool list, consent without the
server, or a token the front door does not know. Say which.
