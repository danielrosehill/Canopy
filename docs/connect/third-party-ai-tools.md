# Setup guide: Canopy in ChatGPT, claude.ai and other AI tools

How to give a hosted AI tool your whole Canopy (every skill and tool, scoped to one person) through one connector,
so that a plain request ("print a label that says X") just works. Proven with ChatGPT and claude.ai on
2026-11-28. The background and the alternatives are in [hosted and web clients](saas-and-web-clients.md).

## The shape: one connector per person

```
ChatGPT ─┐                          ┌─ portal server "canopy-<person>" ── bearer token ─┐
claude.ai┼─ OAuth ─ one URL ────────┤   (access policy: that person only)               ├─ front door :8070/mcp
any OAuth┘   (MCP server portal)    └───────────────────────────────────────────────────┘   profile <person>
client                                                                                        │
                                                                     search/read/list ── catalogue (skills, tools)
                                                                     call_tool ───────── hub ── gateway ── upstreams
```

- **The client sees five tools**: `search`, `read`, `list`, `list_servers`, `call_tool`, plus the MCP Skills
  extension. Everything else is found by search, so the client's tool list never grows and never goes stale.
- **One connector per person, and only one.** A client with several home connectors has to guess which one a
  request is for, and guesses wrong: a chat answered from an older, narrower connector and said there was "no
  skill" for printing. With one connector, ChatGPT picked it for a plain request without being told.
- **No vendor tunnel needed.** ChatGPT and claude.ai both use the same OAuth URL. OpenAI's and Anthropic's tunnels
  are only for a server with no public OAuth endpoint.

## 1. Server

1. Run the front door: `docker compose --profile front up -d front`. Bind it where the ingress reaches it
   (`CANOPY_FRONT_BIND`), never to the internet directly.
2. Profiles: `agent-data/config/spaces.json` ([spaces](../spaces.md)): one profile per person, the union of the
   spaces they belong to. Each gets `data/front-tokens/profile-<name>.token`.
3. While the hub fronts an existing gateway: `FRONT_SERVERS` with one transparent entry for a group that holds every
   profile's tools, and the same group as the `catalogue` service's `TOOLS_GROUP`, so search finds them.
4. Check it before any client: `front_probe.py` (`canopy-manager/scripts`) with your golden cases. Reach and use
   must pass, and retrieval should be close to 1.0: a client cannot beat it.

## 2. Ingress (Cloudflare MCP server portal)

[Pattern 4](../deployment-patterns.md#4-cloudflare-mcp-server-portal-for-hosted-clients) in detail; the parts
specific to the front door:

- One **portal server per person**: `hostname` = the front door's `/mcp`, `auth_type: "bearer"`,
  `auth_credentials` = that profile's token. Its Access application admits only that person.
- One **portal per person** holding only their server is simplest: nothing else on it competes. Name and
  describe it for what it is ("Canopy for Alex: every home tool and skill").
- Dynamic client registration on, with both vendors' redirect URIs allowed:
  `https://claude.ai/api/mcp/auth_callback`, `https://claude.com/api/mcp/auth_callback`,
  `https://chatgpt.com/connector_platform_oauth_redirect`, `https://chatgpt.com/connector/oauth/*`.

## 3. ChatGPT

1. Settings → Plugins (older: Connectors) → add a custom app with the portal URL, OAuth. Connect, sign in, tick the
   person's server on the consent page.
2. **Name it "Canopy"** and give it a description that says when to use it and how: "Everything on my home server:
   printing documents and labels, Home Assistant, inventory, chores, memory, email, ... Call search first with the
   task in plain words, read what it returns, then call_tool." ChatGPT picks apps by this text.
3. Remove or disconnect other connectors that cover the same ground, for that person.
4. After a server change: the app's **Refresh tools**. A workspace (published) app keeps its tool list until an
   admin refreshes it.
5. Test: `test-chatgpt` in `canopy-manager`. When it fails: `debug-chatgpt`.

## 4. claude.ai

1. Settings → Connectors → Add custom connector, the same portal URL. Connect, sign in, tick the server. On Team and
   Enterprise an owner adds it first.
2. Turn it on in chats (the + menu → the connector's toggle).
3. After adding a server to the portal: disconnect and connect again, and tick it.
4. Test: `test-claude-ai`. When it fails: `debug-claude-ai`.

## 5. Other front ends

Anything that speaks Streamable HTTP MCP can use the front door. A client you run yourself (Open WebUI, LibreChat,
a self-hosted assistant) can skip the portal: send `Authorization: Bearer <profile token>` straight to the front
door on your network. A **dedicated assistant** (a ChatGPT Workspace Agent or custom GPT called "Canopy", or a
self-hosted assistant) whose only job is to act on the home server is the most reliable front end: its
instructions can say "for every request, search Canopy first", and it has no other apps to choose between. It
complements the general chat app rather than replacing it.

## What went wrong on the way (2026-11-28)

| Symptom | Cause | Fix |
| --- | --- | --- |
| "There's no skill for printing a custom label" | The chat used an older connector with no catalogue and no print tool | One connector per person |
| "Only QR-code labels are available" | That connector's workspace tool list predated the print server | Refresh tools; or the front door, whose list never changes |
| Works with `@Canopy`, not without | Several home connectors competing | Remove the others; name and describe the app for its job |
| Portal sync 401 | `spaces.json` retired the `default` token | Each portal server sends its profile's token |
| Search finds the skill but not the tool | The catalogue indexed tools from a narrower group | `TOOLS_GROUP` on the catalogue = the front door's group |
| Printed on the wrong printer | The request named none, and the hub's default is another printer | Set the hub's default to the preferred one; Canopy's standing preferences are only read when the client searches for them |
