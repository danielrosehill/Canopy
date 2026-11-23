---
name: migrate-memory
description: Move Claude Code's file-based auto-memory (~/.claude/projects/*/memory) into a memory store on the gateway, point CLAUDE.md at it, and turn auto-memory off. Use on "migrate my memory", "boot memory off Claude", "move memory into the store", or when MEMORY.md takes up baseline context.
---

# Move auto-memory into a memory store

Auto-memory loads its `MEMORY.md` index in every session and lives on one machine. In a store it is recalled on
demand, from any device. Needs a memory MCP upstream with `remember`, `recall` and `list_memories`.

The script never changes or deletes local files. `--namespace` is the namespace in the store;
`--prefix` is the tool prefix as the gateway names it (default `memory__`).

1. **Back up** every `~/.claude/projects/*/memory` folder to a dated directory outside `~/.claude`.
2. **Scan** the backup for secrets (`gitleaks dir <backup>`). A real credential is taken out before the push.
3. **Namespace.** List the store's namespaces; create `agent-memory` (or the user's name for it) with a description.
4. **Plan and pilot:**
   ```bash
   S=skills/migrate-memory/scripts/migrate_memory.py
   python3 $S plan
   python3 $S push --url "$CANOPY_MCP_URL" --namespace agent-memory --limit 5
   ```
   Set `MCP_TOKEN` if the server needs a token. Read one back with `recall`.
5. **Push and verify:** `push` without `--limit`, then `verify` (exit 0, `missing` empty). Re-running is safe: it stores
   only what is missing. `unindexed` entries are saved but not searchable until the store is reindexed.
6. **Prove recall works:** ask three questions in plain words whose answers are in known files.
7. **CLAUDE.md**, with the backup kept beside it. Add, with the real namespace:
   > Agent memory is namespace `agent-memory` on the gateway `memory` server. `recall` there at the start of a
   > task; save durable lessons there with `remember`. Don't use built-in file memory.
8. **Turn auto-memory off:** `"autoMemoryEnabled": false` in `~/.claude/settings.json`. Without this the harness keeps
   telling the agent to write files, whatever CLAUDE.md says.
9. **Delete the local folders, only with a yes**, after running `push` and `verify` once more (another session may
   have written since). Delete only folders where nothing is missing.
10. **Report** the counts and the new `/context` baseline from a fresh session. Repeat on each machine.

Index files (`MEMORY.md`, `index-*.md`) are left out: they only list other memories. If yours hold facts of their
own, push those first by moving them into a memory file. `--skip REGEX` leaves out more.

Feedback memories that must apply before any task is known don't survive as recall: keep those in CLAUDE.md or as
Canopy habits and preferences.
