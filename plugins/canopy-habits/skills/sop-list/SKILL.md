---
name: sop-list
description: Show your SOPs in Canopy, everything or only stale ones, or answer whether one exists for something. Use when the user asks what SOPs they have, whether a procedure exists, which need reviewing, or wants an overview before deciding what to write next.
---

# List SOPs

Everything lives in Canopy: read and write only through the catalogue tools, never local files. The helper script is `$PLUGIN/scripts/habits.py` (standard library only; it never touches Canopy or the network except to clone a library). `$PLUGIN` is `${CLAUDE_PLUGIN_ROOT}` when set; otherwise `catalogue__read` the id `file:canopy-habits/scripts/habits.py`, save the text to a temp dir and run that. Records are `habit:user/<id>` and `sop:user/<id>`.

`catalogue__list(kind="sop", marketplace="context")` gives id, trigger and description: that is the table. Do not read
every SOP to summarise it. When the user asked whether one exists for something, `catalogue__search` it and answer that:
name the match, or say there is none and offer `sop-write`.

## Staleness pass (offer it when reviewing)

`last_verified` and `frequency` are in the frontmatter, so `catalogue__read` only the SOPs you need to date. Read age
against frequency: monthly and unverified for eight months is suspect; yearly and unverified for thirteen months is
normal; rare and unverified for three years is fine. For each stale one offer: verify on the next run, edit now, or
retire (`sop-remove`). Do not re-date anything here: a review is not a run.

Also report, as a short list: overlapping triggers (the dispatcher picks one arbitrarily), triggers that restate the
title, and SOPs with an empty `## If it goes wrong` (written but never run in anger).
