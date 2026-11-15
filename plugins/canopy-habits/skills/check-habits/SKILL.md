---
name: check-habits
description: Audit your habits, SOPs and CLAUDE.md wiring. Validates records, summary fidelity, overlap, wrong-kind and stale-block problems, and reconciles hand-written prose in CLAUDE.md against the records. Use after editing habits or SOPs, after hand-editing CLAUDE.md, or on "check/audit the habits" or "a habit isn't in force".
---

# Check habits and SOPs

Everything lives in Canopy: read and write only through the catalogue tools, never local files. The helper script is `$PLUGIN/scripts/habits.py` (standard library only; it never touches Canopy or the network except to clone a library). `$PLUGIN` is `${CLAUDE_PLUGIN_ROOT}` when set; otherwise `catalogue__read` the id `file:canopy-habits/scripts/habits.py`, save the text to a temp dir and run that. Records are `habit:user/<id>` and `sop:user/<id>`.

## Mechanical

Build the records file as in `sync-habits`, then:

```bash
python3 "$PLUGIN/scripts/habits.py" check /tmp/records.json [--target FILE] [--json]
```

Fails on bad frontmatter, an id not matching the record name, bad `stance` or `status`, a missing or over-long summary, a
duplicate title, an invalid SOP, a block over budget, a missing, duplicated or legacy block, a hand-written `## Habits`
or `## Procedures` heading outside the block, and a block stale against Canopy. It warns on a dangling `related:`.
Repair a stale block with `sync-habits`. A duplicate marker or hand-written habit prose is not repaired mechanically.

## Judgement

Read the habit records and report what the script cannot see:

1. **Summary fidelity.** Does each summary carry its snippet's rule and main exception? The most important check.
2. **Overlap.** Two habits saying one thing drift apart; name the pair and which absorbs the other.
3. **Contradiction.** Check that an explicit exception resolves tension such as "push without asking" against "confirm
   before publishing".
4. **Wrong kind** ([`references/habit-or-sop.md`](../../references/habit-or-sop.md)): a habit with numbered steps is an
   SOP; a habit that matters for one kind of task needs a `trigger`; an SOP that restates a habit should cite it.
5. **Not a habit:** environment facts belong in CLAUDE.md or a `context` record.

## Reconcile CLAUDE.md

Sort every difference between the instruction file and the records into three buckets before changing anything:

1. **Prose with no record**: a hand-written directive that meets the bar for a habit. Propose `capture-habit`, quoting
   it; a numbered procedure is an SOP (`sop-write`). Environment facts stay where they are.
2. **A stale block**: regenerate with `sync-habits`.
3. **Prose duplicated by the block**: prefer the user's wording where it is sharper (update the record, sync, then delete
   the prose). Never delete a hand-written section without showing the exact text removed and what replaces it.

Report one table (habit/SOP or bucket, problem, proposed fix), wait, apply the approved items in this order: update
records, `sync-habits`, then remove superseded prose. Re-run the check after.
