---
name: migrate-context
description: Move the user's standing instructions (CLAUDE.md habits, rules, procedures, preferences, and side files of background notes) into their Canopy, so every device and agent gets them through search instead of a machine-bound file. Use on "migrate my context", "move my CLAUDE.md into Canopy", "my rules are stuck on this machine", "save this rule in Canopy", or when setting up Canopy on a device whose CLAUDE.md is large.
---

# Move your context into Canopy

Canopy keeps the user's context in a git repo on the server (`$CANOPY_DATA/context`), indexed beside the skills.
`catalogue__search` returns a matching record before a task, on any device, with one connector. Writes go through
`catalogue__context_write`, and each one is a commit.

| Kind | Folder | What goes in it |
| --- | --- | --- |
| `habit` | `habits/` | How to work, whatever the task: verify, document, ship |
| `sop` | `sops/` | A procedure for a recurring job, step by step |
| `preference` | `preferences/` | A rule with a trigger, including guardrails: "repos about X go in org Y", "never X when Y" |
| `context` | `context/` | Background to read when a task touches it: a network, accounts, services, past fixes |

Rules that hold in every session whatever the task (tone, how to report, the user's name for things) have nothing
to search on. Those stay in CLAUDE.md.

## Steps

1. **Check the target.** `catalogue__search` "context write" or `list(marketplace="context")`. If
   `catalogue__context_write` is not in your tools, the server has writes off (`CANOPY_CONTEXT_WRITE=false`) or
   predates this feature: stop and say so.
2. **Inventory, read-only.** The user-level `CLAUDE.md` (`~/.claude/CLAUDE.md`, and `~/CLAUDE.md` if the user keeps
   one), every file it points at for detail (a context folder, `@` imports), and any habits, SOPs or preference
   folders it names. Skip generated blocks (between `<!-- x:begin -->` markers) whose source you can find: migrate
   the source instead. Note each file's size.
3. **Classify into a plan.** One row per proposed record: kind, name (lowercase-with-hyphens), the one-sentence
   description (the trigger and the rule: it is all search sees), and the source lines. A side file of background
   usually becomes one `context` record as is; a CLAUDE.md section of several rules becomes several records. Mark
   what stays in CLAUDE.md, and why. Existing records (`list(marketplace="context")`) are updated, not duplicated.
4. **Show the plan and wait for a yes.** It changes what every agent reads. Flag anything that looks like a secret
   value (a token, a password): it does not go in, ever; reference the secret's name instead.
5. **Write.** `catalogue__context_write(kind, name, text, description)` for each row. Keep the original wording; add
   frontmatter only through `description`. Large background files are fine (up to 200 KB each).
6. **Verify each one.** `catalogue__search` the trigger in plain words and check the record comes back, then
   `catalogue__read` one id and compare it to the source. A record that search cannot find has a weak
   description: rewrite it and write again.
7. **Shrink CLAUDE.md, only after step 6 passes and with a yes.** Back the file up first
   (`<file>.bak-YYYY-MM-DD`). Remove what moved, keep what stays, and add one line near the top:
   "Standing instructions and background notes are in Canopy: `catalogue__search` before a task returns the ones
   that apply; read and follow them." Leave local side files in place until the user has used another device
   for a while, then offer to delete them.
8. **Report** in one table: record id, kind, source, verified. Then the CLAUDE.md before and after, in lines.

## Later

To save a new rule from any device, `catalogue__context_write` it and show the user what was saved. The server
pushes to `CONTEXT_REMOTE` hourly if one is set (`canopy context push` to do it now), and both backups cover it.
