---
name: adopt-habit
description: Adopt one habit, several, or the whole example library into your Canopy, with tunable values such as a spend limit filled in and the source library recorded. Use when the user wants one of the example habits, says "adopt/import the library", "import all the habits", or is setting up habits for the first time.
---

# Adopt library habits

Everything lives in Canopy: read and write only through the catalogue tools, never local files. The helper script is `$PLUGIN/scripts/habits.py` (standard library only; it never touches Canopy or the network except to clone a library). `$PLUGIN` is `${CLAUDE_PLUGIN_ROOT}` when set; otherwise `catalogue__read` the id `file:canopy-habits/scripts/habits.py`, save the text to a temp dir and run that. Records are `habit:user/<id>` and `sop:user/<id>`.

Every adopted habit adds a line to every session, so adopt what the user picks. Libraries: see `list-library`
(`--repo owner/repo[@ref]`, `$CANOPY_HABITS_LIBRARIES`, default `danielrosehill/Agent-Habits`).

## One or a few

```bash
python3 "$PLUGIN/scripts/habits.py" library --json                     # choose
python3 "$PLUGIN/scripts/habits.py" adopt spend-approval-limit --set APPROVAL_LIMIT='$20' > /tmp/habit.md
```

`adopt` renders `{{VARIABLES}}` (defaults unless `--set`), drops the `variables:` block and records `library: <id>` and
`library_version: <revised date>` plus a `source:` line. Ask for the value when its default is a judgement call
(`spend-approval-limit` has `APPROVAL_LIMIT`, `REAPPROVAL_MARGIN`). Skip any id that already exists in Canopy unless
the user wants it replaced (then diff first; a local edit would be lost). Write each with
`catalogue__context_write("habit", "<id>", <text>, <the summary line as description>)`.

## Import all

```bash
python3 "$PLUGIN/scripts/habits.py" adopt --all --skip <ids already present, comma-separated> --set NAME=VALUE ... > /tmp/import.json
```

Prints `{"habits": [{"name", "text", "description"}, ...]}` with values rendered. Ask once for the values that are
judgement calls, and say how many lines this adds to the block and that `check-habits` will flag overlap with what is
there. Then one `context_write` per record.

## After

`catalogue__read` one record back to verify, then run `sync-habits`. The library is in the first person
("when I ask..."), which becomes the adopter's own voice. If CLAUDE.md prose already covers the same ground, say so
and offer `check-habits`. Changes reach new sessions, not this one.
