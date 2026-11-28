# Hosted and web clients: ChatGPT, claude.ai, Open WebUI and others

How to use a home gateway from clients that are not the `canopy-connector` plugin in Claude Code. Canopy ships none
of this: each route needs its own scoping, and ChatGPT needs its own marketplace for skills. This page is
the map. Vendor details were read from their docs on 2026-11-05 and change often, so follow the links.

| Client | Route | Status |
| --- | --- | --- |
| ChatGPT | OpenAI's own tunnel, or an OAuth ingress | Neither run with Canopy yet. In the deployment Canopy came from, the ingress is live and the tunnel was retired over a sharing limit, below |
| claude.ai | Custom connector to an OAuth ingress. Anthropic's tunnel on Enterprise | Not run with Canopy yet. The ingress is live in that deployment. The tunnel has not been tried |
| Open WebUI, LibreChat and similar | Direct, with a bearer token | From their docs, not yet run |

**Start with the [setup guide](third-party-ai-tools.md):** one OAuth connector per person, in front of the
front door, used by ChatGPT and claude.ai alike. The rest of this page is the background and the alternatives.

## The design: one lane per vendor

One ingress for every hosted client sounds tidier, but each vendor has its own way in, and going with it
is less work than fighting it. So give each vendor its own lane, side by side, and let them share only
what sits behind: a gateway group.

```
ChatGPT    --> OpenAI's tunnel client  --+
claude.ai  --> OAuth ingress           --+--> a gateway group for hosted clients --> upstreams
Open WebUI --> bearer token            --+
```

### Recommended: the front door behind the lane

Point the lane at the **front door** (compose service `front`, `src/canopy/front.py`) instead of a gateway group.
Every client then gets the same five tools (`search`, `read`, `list`, `list_servers`, `call_tool`) and the MCP
Skills extension ([SEP-2640](https://modelcontextprotocol.io/seps/2640-skills-extension)), so a hosted client
finds and follows skills the way Claude Code does, and its tool list stays at five however much it can reach.
Neither ChatGPT nor claude.ai calls `skills/list` itself (November 2026); they reach skills through `search` and
`read`, which work on any client.

1. Start it: `docker compose --profile front up -d front`. It listens on `${CANOPY_FRONT_BIND}:${CANOPY_FRONT_PORT}`
   (default `127.0.0.1:8070`); bind it to the address your ingress reaches.
2. Its token is `data/front-tokens/profile-<name>.token`, made on first start (`default` with no `spaces.json`,
   which sees everything). Profiles and scope: [spaces](../spaces.md).
3. Until the hub replaces the gateway, front the gateway you already run: set `FRONT_SERVERS` to a file with one
   transparent entry, so tools keep the `<server>__<tool>` names the catalogue indexed:

   ```json
   {"servers": [{"name": "gateway", "transparent": true, "transport": "streamable_http",
                 "url": "http://gateway:8080/v0/groups/<group>/mcp"}]}
   ```

4. The ingress must send `Authorization: Bearer <token>`. An ingress that cannot add a header needs a proxy that does.

Without the front door, make a group with only what that client should see, as in
[pattern 4](../deployment-patterns.md#4-cloudflare-mcp-server-portal-for-hosted-clients). Never expose
`/mcp`, or a group holding admin or host-level tools. A hosted client runs on someone else's cloud, and
a model acting on untrusted text can call whatever the group allows.

## ChatGPT

[Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) is OpenAI's own
route for a server on a private network. `tunnel-client` runs beside the gateway and only dials out, so
nothing needs a public address.

1. Check your plan and workspace permissions allow custom MCP servers:
   [OpenAI's help article](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt).
2. Create a tunnel in [Platform settings](https://platform.openai.com/settings/organization/tunnels) and
   associate it with your ChatGPT workspace. Without that, it does not appear in ChatGPT.
3. Install [`tunnel-client`](https://github.com/openai/tunnel-client/releases/latest) on the server, then:

   ```bash
   export CONTROL_PLANE_API_KEY=...
   tunnel-client init --profile canopy --tunnel-id tunnel_... \
     --sample <an HTTP sample from the docs> --mcp-server-url http://127.0.0.1:8080/v0/groups/hosted/mcp
   tunnel-client doctor --profile canopy --explain
   tunnel-client run --profile canopy     # keep this running, as a service
   ```

4. In [ChatGPT plugins](https://chatgpt.com/plugins), choose **Add custom MCP server**, then **Tunnel**,
   pick the tunnel, set authentication, create it as a plugin, and install the plugin.

Know before you start:

- **Canopy's token is an open problem here.** ChatGPT [offers](https://developers.openai.com/api/docs/guides/custom-mcp-server)
  OAuth or no authentication, and no static bearer token. A tunnel is only transport, so the default
  enterprise mode will refuse a client with neither. The likely answer is a small proxy beside
  `tunnel-client` that adds the token, but that is untested.
- **Sharing.** In the source deployment the tunnel's app stayed a personal draft: publishing it to the
  workspace returned 403 (October 2026), so nobody else could use it. That is why that deployment moved
  ChatGPT to the OAuth ingress. If more than one person needs access, test publishing first.
- **Tool lists do not update by themselves.** After changing the group, refresh the app in ChatGPT.
- **Skills are a separate job.** The MCP connection brings tools. A plugin's `mcp.json` did not give ChatGPT
  a web connection, so the connection is one plugin and skills, in ChatGPT's own marketplace, are another.
- **Limits.** A request through the tunnel has 120 seconds, including time in the queue, and 10 MiB.

## claude.ai

A [custom connector](https://claude.com/docs/connectors/custom/remote-mcp) connects from Anthropic's cloud,
so the URL must be reachable from the internet, unless you use Anthropic's tunnel.

1. Put an OAuth ingress in front of the group.
   [Pattern 4](../deployment-patterns.md#4-cloudflare-mcp-server-portal-for-hosted-clients) is the proven one.
2. Add it under **Customize > Connectors** on individual plans. On Team and Enterprise, an owner adds it
   under **Organization settings > Connectors**, and members then sign in as themselves.
3. Claude detects OAuth by itself. Dynamic client registration worked in the source deployment.

[MCP tunnels](https://claude.com/docs/connectors/mcp-tunnels/overview) are the equivalent of OpenAI's tunnel,
but they are a research preview, for Enterprise by request. For most home users the ingress is the route.
Static **Request headers** would suit Canopy's token, but they are in beta and limited to some organizations.

## When a hosted client fails

Skills `debug-chatgpt` and `debug-claude-ai` in `canopy-manager` walk the hops. Seen on a front door deployment
(2026-11-28), in order of how often they fooled us:

- **The chat used another app.** ChatGPT picks apps per chat. A chat with no app selected answered from an older
  household app and said there was "no skill" and "only QR-code labels". Selecting the app with `@` fixed it.
- **A stale tool list.** A ChatGPT workspace app keeps the tool list it had when published; refresh it in the
  admin console. Behind a front door the list is always five tools.
- **Consent without the new server.** A grant covers only the servers ticked when it was made: reconnect.
- **A retired token.** Adding `spaces.json` retires the `default` profile; the ingress gets 401 until it sends a
  profile's token.

## Open WebUI, LibreChat and other clients you run yourself

These are the easy case. If a client speaks Streamable HTTP and can send a header, it needs no ingress:
run it on the same machine or private network, and give it a token.

- **URL:** `http://<gateway>:8080/v0/groups/<group>/mcp`. From a container on the same host, use
  `host.docker.internal` or join the `canopy` network, since `localhost` is the container itself.
- **Token:** `canopy token`, or better a client of its own, so that it can be revoked alone.
- **[Open WebUI](https://docs.openwebui.com/features/extensibility/mcp):** from v0.6.31, an admin adds the
  server under Admin Settings, as type MCP (Streamable HTTP), with Bearer auth and the token as the key.
- **[LibreChat](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/mcp_servers):**
  a `streamable-http` entry under `mcpServers`, with `headers`, `Authorization: Bearer ${CANOPY_TOKEN}`, and
  `requiresOAuth: false`.

For anything else, the same two facts apply: the URL and the header. A client that can only do OAuth takes
the ingress route, and one that cannot send headers needs a proxy that adds them.

Exposing one of these web apps beyond your own network is a separate decision: it holds a token to your
tools, so it needs a login of its own.
