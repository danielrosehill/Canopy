---
name: sync-habits
description: Rebuild the generated habits and procedures block in CLAUDE.md or AGENTS.md from the active habit and SOP records in Canopy, with a size-budget check. Use after any habit or SOP change, on "sync habits", "update the CLAUDE.md block", when a new device's CLAUDE.md block is missing or stale, or when a habit seems not to be in force.
---

# Sync the block

Everything lives in Canopy: read and write only through the catalogue tools, never local files. The helper script is `$PLUGIN/scripts/habits.py` (standard library only; it never touches Canopy or the network except to clone a library). `$PLUGIN` is `${CLAUDE_PLUGIN_ROOT}` when set; otherwise `catalogue__read` the id `file:canopy-habits/scripts/habits.py`, save the text to a temp dir and run that. Records are `habit:user/<id>` and `sop:user/<id>`.

CLAUDE.md carries one marker-delimited block (`<!-- agent-habits:begin -->` ... `<!-- agent-habits:end -->`): `## Habits`
with one summary line per active habit (triggered ones grouped under "These apply only when their trigger does") and
`## Procedures` with one trigger line per active SOP. The full texts stay in Canopy, read on demand.

## 1. Fetch the records

`catalogue__list(kind="habit", marketplace="context")` and `kind="sop"` for the ids (retired and archived records are not
indexed, so they drop out). Then `catalogue__read` all ids in one call. Build a records file
`{"habits": [{"name", "text"}], "sops": [{"name", "text"}]}`. Habits can be frontmatter only (the block reads nothing
below it, so the file stays small). SOPs need their full text: validation rejects an SOP with no `## Steps` section,
and then nothing syncs. Skip non-record files such as `README` and `INDEX` (first used 2026-11-15).

## 2. Find the targets and preview

```bash
python3 "$PLUGIN/scripts/habits.py" where                       # ~/CLAUDE.md, ~/.claude/CLAUDE.md, AGENTS.md ... and which have a block
python3 "$PLUGIN/scripts/habits.py" sync /tmp/records.json     # prints the block, size against the 7,000-character budget
python3 "$PLUGIN/scripts/habits.py" sync /tmp/records.json --target ~/CLAUDE.md   # diff, dry run
```

Sync the files that already have a block. A file with none is only added to once the user says where it goes (it appends
at the end). A file with a begin marker missing or a duplicated marker stops the script: show the user and fix by hand.

## 3. Write

`--write` replaces the block, after saving `<file>.bak-<timestamp>`. Over budget means too many always-on habits: suggest a
`trigger` for some, or merge or retire, rather than raising the budget. Then `check-habits` to confirm. Changes reach new
sessions, not this one.
