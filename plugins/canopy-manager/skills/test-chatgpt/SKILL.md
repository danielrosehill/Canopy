---
name: test-chatgpt
description: Test ChatGPT against Canopy end to end - reach (sees the front-door tools), retrieval accuracy (finds the right skill or tool for golden queries) and use (does a plain task without being told which app) - by driving the user's browser and checking the server. Use on "test ChatGPT", "does ChatGPT work with Canopy", "check ChatGPT can find X", after changing the ChatGPT app, the portal or the front door.
---

# Test ChatGPT with Canopy

The tests and pass criteria: `references/hosted-client-tests.md` (plugin root). Run its server baseline first.
Browser: `claude-in-chrome`, a new tab. When a test fails, switch to `debug-chatgpt`.

## ChatGPT specifics

- **Temporary chat for every test** (`https://chatgpt.com/?temporary-chat=true`): no history, and no unsent draft
  of the user's in the composer. Check the composer is empty before typing.
- **Reach and retrieval tests name the app**: type `@<app name>` and press **Tab** to attach it (clicking the
  suggestion does not always attach it; check a chip appears). The use test must **not** name it.
- ChatGPT shows "Worked for Ns" while it runs; a tool-heavy turn takes 20 s to 2 min. Poll the page text, and read
  the front door log for the calls in the meantime.
- A ChatGPT workspace app keeps its tool list until refreshed (Settings → Plugins → the app → **Refresh tools**);
  a test right after a server change can fail on the old list.
- What decides whether ChatGPT picks Canopy unprompted: how many connected apps compete for the request, and the
  app's name and description. One app per person, named for what it does ("Canopy: everything on the home
  server: printing, ..."), is what made the use test pass (2026-11-28).

## Report

A table: test, pass or fail, seconds, evidence (front door log line, target system's record). Then the retrieval
hit rate next to the server baseline's.
