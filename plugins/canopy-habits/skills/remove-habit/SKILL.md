---
name: remove-habit
description: Retire one of your habits so it leaves the CLAUDE.md block, or delete it from Canopy when explicitly asked, then sync the block. Use when the user says to drop, remove, delete, retire or stop applying a habit.
---

# Remove a habit

Everything lives in Canopy: read and write only through the catalogue tools, never local files. The helper script is `$PLUGIN/scripts/habits.py` (standard library only; it never touches Canopy or the network except to clone a library). `$PLUGIN` is `${CLAUDE_PLUGIN_ROOT}` when set; otherwise `catalogue__read` the id `file:canopy-habits/scripts/habits.py`, save the text to a temp dir and run that. Records are `habit:user/<id>` and `sop:user/<id>`.

## Retire by default

"Drop it", "remove it", "stop doing that" mean retire. `catalogue__read` `habit:user/<id>`, then write it back with
`status: retired`, `revised: <today>`, a dated line in `## Why` saying why, and the same `summary` as description.
Canopy skips retired records from search and from the block, and the history keeps them. Also remove the id from other
habits' `related:` lists (read each that names it, write it back).

## Delete only when asked

`catalogue__context_delete("habit", "<id>")`. It is a commit on the server, so it can be restored from history: say so.
Remove the dangling `related:` references the same way.

## Then

Run `sync-habits`. If prose elsewhere in CLAUDE.md restates the removed habit, point it out; `check-habits` handles
the cleanup. Changes reach new sessions, not this one.
