---
name: find
description: Find and load a skill, plugin or tool from your Canopy server. Skills are not installed locally. Use before any task that might have a purpose-built skill, on "is there a skill for this", on a plugin:skill name you don't have, or when a tool isn't in your list.
---

# Find a skill or tool

1. `catalogue__search` with the task in plain words. `results` are skills, commands, agents and
   plugins. `tools` are gateway tools, each with how to call it.
2. Prefer a skill. `catalogue__read` its id and follow it. A skill lists its other files: read those
   only when it asks.
3. A tool not in your list: `catalogue__read` its `tool:` id for the schema, then
   `toolbox__call_tool(name, arguments)`.

`weak: true` means nothing strong matched. Stop searching and do the task directly, rather than
rewording. `degraded` means keyword-only search, so try different words before concluding.

Setting up or running the server is the `canopy-manager` plugin, which is searchable here too.
