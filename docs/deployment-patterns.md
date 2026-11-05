# Deployment patterns: reaching Canopy from outside the machine

Canopy binds to localhost by default. These are ways to go further. They are ideas to adapt, not
requirements, and you can run several at once.

For the thinking behind this design, see the
[MCP Deployment Architecture](https://github.com/danielrosehill/MCP-Deployment-Architecture) notes,
a living record of the deployment Canopy came from, and the earlier
[MCP Aggregation Notes](https://github.com/danielrosehill/MCP-Aggregation-Notes).

**What is proven:** patterns 2 to 4 run in that deployment, as observed in October and November 2026,
with a gateway that has no auth of its own. They have not yet been run with Canopy's default
enterprise mode, and Canopy's `tunnel` and `tailnet` profiles are untested. Cloudflare's products
change, so check their current docs.

## Choosing

| Client | Pattern | Who decides access |
| --- | --- | --- |
| Agents on the same machine | 1. Localhost | Canopy token |
| Your own devices | 2. Private network | Network membership, plus Canopy token |
| Scripts and agents elsewhere that can send headers | 3. Tunnel with a service token | Cloudflare Access, plus Canopy token |
| Hosted clients such as ChatGPT and claude.ai | 4. Cloudflare MCP portal | Cloudflare Access, per person |

Keep three questions apart, whichever pattern you use:

- **Who are you?** The network or identity layer in front of Canopy.
- **What can you call?** A gateway group or allow-list.
- **What do upstreams trust?** Their credentials stay on the server, and clients never see them.

## 1. Localhost

The default. Nothing to do.

## 2. Private network with Tailscale

```
your devices --(WireGuard, tailnet)--> Canopy gateway
```

Each device joins the tailnet, and only members can reach the gateway. It is the fastest route, with
nothing public to attack, and the right default for your own machines.

Either run Tailscale on the host and set `CANOPY_BIND` to its tailnet address, or use the `tailnet`
profile. The profile gives Canopy its own tailnet identity, and needs `TS_AUTHKEY`.

Devices that cannot join the tailnet, and hosted clients, need pattern 3 or 4.

## 3. Cloudflare Tunnel with Access service tokens

```
client (Authorization + CF-Access-* headers)
  --> Cloudflare edge: Access checks the service token
  --> tunnel (outbound from your server, so no open ports)
  --> gateway
```

For non-browser clients that can send custom headers, and as a fallback when the tailnet is not
usable. Run the `tunnel` profile with `TUNNEL_TOKEN`, and point the ingress rule at
`http://gateway:8080`.

**A hostname needs three things, and a missing one looks like something else:**

| Missing | What you see |
| --- | --- |
| Ingress rule in the tunnel config | A Cloudflare error page (such as 1033) or 404 |
| Proxied DNS CNAME to the tunnel | The name does not resolve, while the tunnel config looks healthy |
| Access application for the hostname | **No error at all: the origin is public.** |

Create the Access application first. A request with no service token gets 403 from Cloudflare, which
means it is working, not that the server is down. If the proxy runs on the same host and forwards to
a loopback address, see MCPJungle's note on `Host` headers. The `tunnel` profile reaches the gateway
over the stack network, so this does not apply to it.

## 4. Cloudflare MCP server portal for hosted clients

Hosted clients run in someone else's cloud. They cannot join your tailnet, and usually cannot send
custom headers, so they sign in with OAuth instead.

```
ChatGPT / claude.ai
  |  OAuth 2.1 + PKCE, dynamic client registration. Nothing is pasted into the client.
  v
https://mcp-portal.example.com/mcp      Cloudflare MCP server portal
  |  Cloudflare Access: each person signs in as themselves, under your policy
  v
Cloudflare Gateway --> tunnel private route to the server's private address
  v
http://<private-address>:8080/v0/groups/hosted/mcp      one gateway group
  v
upstreams, which keep their own credentials on the server
```

The portal decides who you are. The group decides what you can call. Short-lived access tokens are
refreshed by the client, so no long-lived secret sits in the hosted service.

To set it up:

1. Make a gateway group with only what hosted clients should see. Prefer `included_tools`, as an
   allow-list. **Never point a portal at `/mcp` or at a group holding admin or host-level tools.**
2. Give the tunnel a private route to the server's address, and set `CANOPY_BIND` so that the gateway
   listens there.
3. Create the portal on its own hostname. Its DNS points at Cloudflare's portal service rather than
   the tunnel, so sharing a hostname with pattern 3 would cut that route off.
4. Add a portal server for the group, with its URL on the private address and Gateway routing on.
5. Create an Access application for that server. Without one, the portal reports that no servers are
   allowed.

**What to expect in operation:**

- **Use one portal server for shared tools.** A client's grant covers only the servers ticked when it
  connected, so each new server means everyone consents again. With one server, a new tool just goes
  into its group.
- **Tool lists are cached in two places.** The portal keeps its own copy, and may need a forced sync
  through its API. ChatGPT also freezes the list when an app is published, so an admin must refresh
  it there.
- **Updating a portal's servers replaces the whole list.** Send all of them, not just the new one.
- **Do not reach the upstream through a public tunnel hostname.** That setup passed the admin sync and
  listed its tools, then every per-user connection failed with HTTP 530. Use the private route.
- **Changing a server's URL may not take effect,** even though the call succeeds. Delete the server
  and recreate it with the same id.

**Open question with enterprise mode:** in the proven setup the portal calls the gateway with no
credential, and the private route is the only protection. Canopy's enterprise mode will refuse those
calls. Portal servers have a bearer option, which should carry a Canopy client token, but this has not
been tested over a private route. Test it before turning enterprise mode on for a server that uses
this pattern.

## Whichever you choose

- Give each client its own gateway identity where you can, so one can be revoked alone.
- Remember that the `toolbox` reaches everything its identity can. Keep admin tools out of its scope
  with `TOOLS_GROUP` or an allow-list, or a narrow group gains little.
- The web UI has no login. Put it behind one of these patterns before exposing it, and add its name to
  `UI_ALLOWED_HOSTS`.
