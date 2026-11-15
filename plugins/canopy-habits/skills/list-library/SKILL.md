---
name: list-library
description: Show the example habits you could adopt from a habit library (default the public Agent-Habits repo; others can be added), marking which you already have. Use when asked "what habits could I use", "what's in the habit library", or when setting up habits for the first time.
---

# List a habit library

Everything lives in Canopy: read and write only through the catalogue tools, never local files. The helper script is `$PLUGIN/scripts/habits.py` (standard library only; it never touches Canopy or the network except to clone a library). `$PLUGIN` is `${CLAUDE_PLUGIN_ROOT}` when set; otherwise `catalogue__read` the id `file:canopy-habits/scripts/habits.py`, save the text to a temp dir and run that. Records are `habit:user/<id>` and `sop:user/<id>`.

A library is a git repo with `library/habits/*.md` and an `index.json`. It is read-only here. Which one:

- `--repo owner/repo[@ref]` for this call;
- else `$CANOPY_HABITS_LIBRARIES`, a comma-separated list of `owner/repo[@ref]` (the user's added libraries);
- else the default, `danielrosehill/Agent-Habits`.

```bash
python3 "$PLUGIN/scripts/habits.py" library --json [--repo owner/repo]
```

It makes a shallow clone into a temp dir and parses every habit. Show id, trigger if any, and the summary; mark the
ones already in `catalogue__list(kind="habit", marketplace="context")` (compare ids and each record's `library:`
frontmatter), and flag any whose library `revised` is newer than the record's `library_version`. When the user has
several libraries, list each under its repo name. To take habits, use `adopt-habit`.
