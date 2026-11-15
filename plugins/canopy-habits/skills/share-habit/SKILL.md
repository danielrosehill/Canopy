---
name: share-habit
description: Contribute one of your habits to a public habit library (default the Agent-Habits repo), depersonalised and with tunable values turned into variables, as a commit if you own the repo or a pull request if not. Use when the user says a habit might help others, asks to publish or share a habit, or after capture-habit produces something generic.
---

# Share a habit to a library

Everything lives in Canopy: read and write only through the catalogue tools, never local files. The helper script is `$PLUGIN/scripts/habits.py` (standard library only; it never touches Canopy or the network except to clone a library). `$PLUGIN` is `${CLAUDE_PLUGIN_ROOT}` when set; otherwise `catalogue__read` the id `file:canopy-habits/scripts/habits.py`, save the text to a temp dir and run that. Records are `habit:user/<id>` and `sop:user/<id>`.

Publishing is outward-facing: show the user the depersonalised text and the diff before pushing.

## Depersonalise

`catalogue__read` `habit:user/<id>`. Write `library/habits/<id>.md` in a clone of the library repo (not the plugin):
a light edit, not a rewrite.

- Keep the first-person voice ("when I ask...") and the concrete examples.
- Remove the user's specifics: private plugins, repos, hosts, accounts, people, regions, internal tool names and dated
  personal incidents in `## Why`. Grep the result for the user's name, hosts and infrastructure terms.
- Make tunable values variables: declare under `variables:` (`NAME: "default"   # meaning`) and use `{{NAME}}` in the
  snippet and in the summary if it mentions the value.
- Set `source: "Habits library"`, `revised: <today>`, `status: active`; drop `library:` and `library_version:`.
- Check prior art: is it already in the library (`list-library`)? Then propose an edit to that habit instead.

## Clone, build, push

```bash
gh repo clone <owner/repo> /tmp/habit-lib     # default danielrosehill/Agent-Habits
python3 "$PLUGIN/scripts/habits.py" library --dir /tmp/habit-lib       # parses every habit: fails on a broken one
python3 "$PLUGIN/scripts/habits.py" index-library --dir /tmp/habit-lib
```

Add a row to the library table in the repo's README if it has one, in the same commit. Then:

- **The user owns the repo** (`gh repo view --json viewerPermission`): commit "Library: add <id>", stage the
  specific paths, push.
- **Otherwise**: `gh repo fork --clone`, branch, commit, `gh pr create` with a one-paragraph description of the rule
  and why it generalises.

In the user's own Canopy record set `library: <id>` and `library_version: <today>` (write it back with
`context_write`) so `list-library` can track it.
