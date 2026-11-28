---
name: debug-claude-ai
description: Work out why claude.ai (web, desktop or mobile) cannot reach Canopy through a custom connector, or reaches it and misses skills or tools, by checking the chat, the connector and each hop to the server. Use on "claude.ai can't see Canopy", "my connector isn't working", "debug claude.ai connectivity", "Claude says there's no skill".
---

# Debug claude.ai connectivity

claude.ai reaches Canopy through a custom connector on an OAuth ingress, from Anthropic's cloud, so the URL must be
public. Find which side fails first: read the chat, then the server's logs at that time.

Server-side checks: `references/hosted-client-checks.md` (plugin root).

## The browser can't do the settings

Claude in Chrome refuses claude.ai's Settings, Customize and connector pages under every permission setting. Those
steps are the user's: give them the exact clicks and values below, then verify from the server. You can still open
chats, read them and run a test prompt in a new chat.

## 1. Read the failing chat

Open the chat. Expand the tool-use blocks: which connector's tools did it call, and with what result? A chat with
no tool call means the connector was off for that chat, or Claude did not think it needed one. Note the time (UTC)
and run step 1 of the server checks: did a request reach the front door at that time?

## 2. The connector exists and is connected (the user does this)

- **Individual plans:** Settings → Connectors → Add custom connector. Name it, URL = the ingress (e.g. the portal's
  `/mcp`). claude.ai finds OAuth by itself (dynamic client registration), then opens the ingress's sign-in and
  consent page: tick the server(s), continue.
- **Team and Enterprise:** an owner adds it under Organization settings → Connectors; each member then connects it
  and signs in as themselves.
- The ingress must allow claude.ai's redirect URIs for dynamic client registration:
  `https://claude.ai/api/mcp/auth_callback` and `https://claude.com/api/mcp/auth_callback`. A registration error
  right after "Connect" is usually this.
- **A grant covers only the servers ticked at consent.** After adding a server to the ingress, disconnect and
  connect again, and tick it.

## 3. The chat has it switched on

In a new chat, open the tools menu (the + or "Search and tools" control under the prompt) and check the connector,
and its tools, are enabled. Then a read-only test: "Connectivity test, change nothing: list the tools you can see
from <connector>, then call its search tool with '<task>'." Pass: the five front-door tools and search results, and
matching requests in the front door log.

## 4. Stale tool list

claude.ai reads a connector's tools when it connects. After the server behind it changes, disconnect and reconnect.
Behind a front door this is never needed: its tool list is always the same five.

## 5. Server side

If the chat used the connector and still failed, or the log shows errors: `references/hosted-client-checks.md`,
steps 2 and 3.

## 6. Finish

Report which hop failed, what changed, and the test that now passes. Record it per step 4 of the server checks.
