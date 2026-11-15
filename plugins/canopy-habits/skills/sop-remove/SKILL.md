---
name: sop-remove
description: Retire an SOP that no longer applies by marking it archived in Canopy, or delete it outright when asked. Use when the user says a procedure is obsolete, that they do not do it that way any more, or a replacement SOP has been written.
---

# Retire an SOP

Everything lives in Canopy: read and write only through the catalogue tools, never local files. The helper script is `$PLUGIN/scripts/habits.py` (standard library only; it never touches Canopy or the network except to clone a library). `$PLUGIN` is `${CLAUDE_PLUGIN_ROOT}` when set; otherwise `catalogue__read` the id `file:canopy-habits/scripts/habits.py`, save the text to a temp dir and run that. Records are `habit:user/<id>` and `sop:user/<id>`.

Default to archiving: a retired procedure is often the only record of how a system used to work. `catalogue__read` it
first and check what the retirement really is:

| Situation | Instead |
| --- | --- |
| One step is wrong | `sop-write` (edit). Do not retire over a renamed flag |
| Superseded | Write the replacement first, list this id in its `supersedes`, then retire |
| Stale, not obsolete | Unverified is not untrue; leave it |
| Genuinely obsolete | Retire |

**Archive:** write the record back with `status: archived`, a line at the top of the body saying why and what replaced
it, and the same description. Canopy stops indexing it, so search and the block no longer show it, and it stays
readable by id and in history.

**Delete:** only on request, after saying what is lost: `catalogue__context_delete("sop", "<id>")`. A server commit, so
recoverable from history.

Then fix other SOPs whose `supersedes` or `## Notes` point at the id, and run `sync-habits`.
