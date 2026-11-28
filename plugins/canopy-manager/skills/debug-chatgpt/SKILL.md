---
name: debug-chatgpt
description: Work out why ChatGPT cannot reach Canopy, or reaches it and does the wrong thing ("no skill for X", a tool it should have, an empty answer), by driving the user's browser and checking each hop to the server. Use on "ChatGPT can't see my tools", "ChatGPT says there's no skill", "debug ChatGPT connectivity", "the ChatGPT connector isn't working".
---

# Debug ChatGPT connectivity

ChatGPT reaches Canopy through an app (custom MCP connector) on an OAuth ingress. Most failures are on the ChatGPT
side and look like server faults. Find out which side first: read the chat, then the server's logs at that time.

Server-side checks: `references/hosted-client-checks.md` (plugin root). Browser: `claude-in-chrome`, in a new tab.

## Rules while driving the browser

- **Never type into a composer that already holds text.** ChatGPT keeps unsent drafts; typing appends to the
  user's words. Test in a temporary chat (`https://chatgpt.com/?temporary-chat=true`), and check the box is
  empty before typing. If you do append, delete exactly what you typed and confirm the draft is intact.
- **Test prompts are read-only.** "Connectivity test, do not change or send anything: list the tools you can see
  from this app, then call its search tool with '<task>'." Never test with the action itself (printing, sending).
- Sign-in, passwords and one-time codes are the user's. Connecting an app and its consent page are fine to drive
  when the user asked you to connect it.

## 1. Read the failing chat

Open the chat link. Expand "Worked for Ns" to see the steps it took. Note:

- **Which app it used.** The answer names tools or behaviour from one app (e.g. "only QR-code labels" = an
  inventory app's label tool, not the printing server). A chat with no app selected may use any connected app, or
  none.
- **When it ran.** A chat from before the connector was fixed shows the old failure forever. Retest in a new chat.

Then step 1 of the server checks: did a request reach the front door or gateway at that time?

## 2. The app is connected, with the right URL

Settings → Plugins (`https://chatgpt.com/settings/plugins-settings`; older UI: Settings → Connectors / Apps). Search
the app's name. Its page shows the URL, `Authorization supported: OAuth`, and **Connect** (not connected) or a
connected account. If not connected: Connect → the ingress's sign-in → its consent page → tick the server(s) →
continue. Expect "… is now connected".

**The consent page decides which servers the app may use.** A grant made before a server was added does not include
it: reconnect and tick it.

## 3. The chat uses that app

ChatGPT picks apps per chat. Type `@<app name>` (or + → the app) before the prompt, in a temporary chat, with the
read-only test prompt. Pass: it lists the front door's five tools (`<server>_search`, `_read`, `_list`,
`_list_servers`, `_call_tool`, prefixed by the portal server's id) and returns search results. The front door log
shows the calls.

If it works with `@` and not without, the fault is app selection, not connectivity: tell the user to pick the app
in chats that need it, or set it up in a Project or Workspace Agent where it is always attached.

## 4. Stale tool list

A ChatGPT **workspace** app freezes its tool list when it is published; a personal (development) app re-reads on
connect. Symptoms: ChatGPT describes tools that changed or misses new ones, while the server checks pass.

- Workspace app: an admin opens the workspace admin console → Plugins/Apps → the app → refresh its tools →
  republish. Members may need to reconnect.
- The fix that lasts: point the app at the front door. Its tool list is always five, so nothing behind it needs a
  refresh.

## 5. Server side

If the chat used the right app and still failed, or the log shows errors: `references/hosted-client-checks.md`,
steps 2 and 3.

## 6. Finish

Report: which hop failed, what you changed, and the test that now passes (with its timestamp in the front door log).
Record it per step 4 of the server checks.
