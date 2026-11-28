---
name: test-claude-ai
description: Test claude.ai (web, desktop, mobile) against Canopy end to end - reach (sees the front-door tools), retrieval accuracy (finds the right skill or tool for golden queries) and use (does a plain task without being told which connector) - in a browser chat, checking the server. Use on "test claude.ai", "does Claude work with Canopy", "check Claude can find X", after changing the connector, the portal or the front door.
---

# Test claude.ai with Canopy

The tests and pass criteria: `references/hosted-client-tests.md` (plugin root). Run its server baseline first.
When a test fails, switch to `debug-claude-ai`.

## claude.ai specifics

- The connector must already exist and be connected (Settings → Connectors): the user adds it, since Claude in
  Chrome cannot open claude.ai's settings pages. You can open chats.
- **A new chat for every test.** Check the connector is on for it: the + menu under the prompt → the connector
  list; a toggle per connector, and a submenu with "Re-select MCP servers / Re-authenticate" when the ingress has
  several servers.
- Reach and retrieval tests name the connector in the prompt; the use test does not.
- claude.ai may ask before running a tool (Manual permissions) or run it (Auto). Approving a real action is the
  user's choice: for a test label, they have said yes when they asked for the test.
- claude.ai reads the connector's tools at connect; after a server change, disconnect and reconnect, or the test
  runs on the old list. Behind a front door the list is always the same five.

## Report

A table: test, pass or fail, seconds, evidence (front door log line, target system's record). Then the retrieval
hit rate next to the server baseline's.
