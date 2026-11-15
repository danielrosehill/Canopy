---
name: sop-run
description: Find the user's SOP for the task at hand in Canopy and follow it. Matches the situation against SOP triggers, reads exactly one procedure and executes it at its declared autonomy. Use whenever a multi-step recurring task comes up (deploying, publishing, filing, onboarding, closing a period) before working the sequence out from scratch, and on "run the SOP for X" or "how do I usually do X".
---

# Run an SOP

Everything lives in Canopy: read and write only through the catalogue tools, never local files. The helper script is `$PLUGIN/scripts/habits.py` (standard library only; it never touches Canopy or the network except to clone a library). `$PLUGIN` is `${CLAUDE_PLUGIN_ROOT}` when set; otherwise `catalogue__read` the id `file:canopy-habits/scripts/habits.py`, save the text to a temp dir and run that. Records are `habit:user/<id>` and `sop:user/<id>`.

The dispatcher: a library of any size costs one table to consult and one record to read.

## 1. Match

`catalogue__search` the situation with `kind="sop"` (or the trigger lines in the CLAUDE.md block). Match the circumstance
against the **trigger**, not the title. One clear match: go on. Several plausible: show them with triggers and ask; never
merge two. A near match: say what it covers and what it does not and ask whether to adapt it or write a new one
(`sop-write`). None: say so, do the task normally, offer `sop-write` afterwards. Never read every SOP.

## 2. Read the one record

`catalogue__read` `sop:user/<id>`. Check `## Preconditions` and `requires` (missing CLI: report at step 0, not step 6).
If `last_verified` is over a year old for its `frequency`, say the date up front and treat surprises as the SOP being
wrong.

## 3. Execute at the declared autonomy

| `autonomy` | Behaviour |
| --- | --- |
| `auto` | Run through, report at the end |
| `confirm` (default) | One confirmation before the first step that changes anything outside this machine |
| `confirm-each` | Ask at every mutating step |
| `manual` | Do not execute: read it back and let the user drive |

The field never widens permission: it does not authorise something the user declined this session, and outward-facing or
irreversible actions still get confirmed. Follow the steps as written; if one is wrong, stop there rather than routing
around it. Say which steps ran, which were skipped and why.

## 4. Verify, then close the loop

Run `## Verification`: an exit code is not verification. Then, with `sop-write` (edit):

- worked as written: set `last_verified` to today, no approval needed;
- worked but drifted: fix the drift and say what changed;
- failed: do not re-date; add what happened to `## If it goes wrong` and tell the user it is known-broken at step n.
