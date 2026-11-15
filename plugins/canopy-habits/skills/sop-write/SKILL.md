---
name: sop-write
description: Write a new SOP in Canopy from a procedure just worked out, or amend an existing one (fix drift, add a failure mode, change trigger or autonomy, re-date last_verified after a clean run). Use on "write this up as an SOP", "remember how we did this", "add this to my procedures", "that SOP is out of date", when the same multi-step sequence has come up twice, and at the end of any SOP run that worked.
---

# Write or edit an SOP

Everything lives in Canopy: read and write only through the catalogue tools, never local files. The helper script is `$PLUGIN/scripts/habits.py` (standard library only; it never touches Canopy or the network except to clone a library). `$PLUGIN` is `${CLAUDE_PLUGIN_ROOT}` when set; otherwise `catalogue__read` the id `file:canopy-habits/scripts/habits.py`, save the text to a temp dir and run that. Records are `habit:user/<id>` and `sop:user/<id>`.

Format: [`references/sop-format.md`](../../references/sop-format.md). Blank start:
[`templates/sop.template.md`](../../templates/sop.template.md). Habit or SOP:
[`references/habit-or-sop.md`](../../references/habit-or-sop.md).

## 1. New or edit

`catalogue__list(kind="sop", marketplace="context")` and `catalogue__search` the job. A near-duplicate is worse than
none: if something close exists, edit it (step 4) and say so. Push back once if it is really reference material, a
one-off, a bare preference or a single command; if the user confirms, write it.

## 2. Draft from the session, not from imagination

Reconstruct from what actually happened: the commands that ran, the ones that failed first, the error text, the fix.

- `trigger`: the circumstance, not a restatement of the title; name the near-miss case.
- `## Steps`: exact commands in the order that worked.
- `## If it goes wrong`: the failures actually hit (symptom, cause, fix). The highest-value section; always write it.
- `## Verification`: an external signal.
- `autonomy`: default `confirm`; `auto` only if wholly reversible; `confirm-each` or `manual` for money, production or
  other people.
- `last_verified`: today only if it ran end to end in this session; otherwise date it and say in `## Notes` it has not
  run as a whole.
- No secrets: reference credentials by name or `op://` path, never values.

## 3. Validate and write

```bash
python3 "$PLUGIN/scripts/habits.py" validate /tmp/sop.md --kind sop --name <id>
```

Then `catalogue__context_write("sop", "<id>", <full text with frontmatter>, <trigger and the job, one sentence>)`;
the description is what search matches. Read it back, run `sync-habits` (SOP trigger lines live in the block), and
show the user the `trigger` line: it decides whether the next session finds this at all.

## 4. Editing

`catalogue__read` the whole record first. Four kinds of edit:

- **Re-date** after a run that went as written: `last_verified: <today>`, nothing else, no approval needed. Only when it
  actually ran; editing is not verifying.
- **Fix drift**: change the step, note in `## Notes` what changed if the old way may still be seen. No commented-out
  old steps; Canopy history has them.
- **Add a failure mode** to `## If it goes wrong`: what it looked like, what it was, what fixed it, and the cause ruled
  out if that cost time.
- **Metadata**: widen or narrow `trigger` (add the near-miss); an `autonomy` downgrade needs no discussion, an upgrade
  needs the user to say so this session. A rename is write-new, delete-old, and fix `supersedes`.

An SOP that has grown two branches is two SOPs: split, cross-link, retire the original. Show a before/after of the
changed lines only, then write and `sync-habits` if the trigger changed.
