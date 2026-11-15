---
name: list-habits
description: List your habits and SOPs from Canopy, ids with one-line rules, or find which habit covers something. Use when asked "what are my habits", "list habits", "which habit covers X", or before deciding whether something is already a habit.
---

# List habits

Everything lives in Canopy: read and write only through the catalogue tools, never local files. The helper script is `$PLUGIN/scripts/habits.py` (standard library only; it never touches Canopy or the network except to clone a library). `$PLUGIN` is `${CLAUDE_PLUGIN_ROOT}` when set; otherwise `catalogue__read` the id `file:canopy-habits/scripts/habits.py`, save the text to a temp dir and run that. Records are `habit:user/<id>` and `sop:user/<id>`.

`catalogue__list(kind="habit", marketplace="context")` gives ids and summaries; add `kind="sop"` for procedures.
Retired and archived records are not indexed, so they do not show: `catalogue__read` an id to see one. For "which
habit covers X", `catalogue__search` it, `catalogue__read` the best hit and quote the relevant sentence of its
snippet. If none covers it, say so, and say whether it would be a habit or an SOP
([`references/habit-or-sop.md`](../../references/habit-or-sop.md)).

Answer with the list: id, one line, and the habit's `trigger` if it has one. Do not read every record to summarise
it; the summary is the summary. The example library is `list-library`.
