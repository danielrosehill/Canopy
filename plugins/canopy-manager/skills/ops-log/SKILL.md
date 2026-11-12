---
name: ops-log
description: Write an entry in the ops notebook, a private git repo of markdown logging updates, maintenance, incidents and decisions across the systems Canopy runs alongside. Also sets one up and searches it. Use after non-trivial infrastructure, account or deployment work, on "log this", "write up what we did", "add to the maintenance log", "what did we change on X", or "set up an ops notebook".
---

# Ops notebook

The notebook is a private git repository of markdown. Its layout, entry types, frontmatter and rules are in the
repository's own `README.md`; read that first, it overrides this file. The starting skeleton is
`examples/ops-notebook/` in the Canopy repository.

## Where it is

1. `$CANOPY_OPS_NOTEBOOK`, a path to a local clone, if set.
2. Otherwise `~/.config/canopy/ops-notebook.json`: `{"path": "<local clone>", "remote": "<git remote>"}`.
3. Neither: ask once for the repository, clone it, and write that file.

`git pull --ff-only` before writing, so entries made on another machine aren't overwritten.

## Write an entry

1. Pick the type: `update`, `maintenance`, `incident` or `decision` (the notebook README defines them).
2. Path: `entries/<YYYY>/<YYYY-MM-DD>-<slug>.md`, date of the work, slug two to four words. Add `-2` only on a
   clash.
3. Start from `templates/<type>.md`. Fill the frontmatter: `areas` from `areas.md` (add a new tag there in the
   same commit if none fits), `systems`, `repos`, `status`.
4. Write from the session: what landed and why, how it was verified, what is still open. For each repo touched,
   `git -C <repo> log -1 --format='%h %s'` goes in the **Repos touched** table. Leave out empty sections.
5. No secrets, tokens, recovery codes or private addresses, ever. If the work involved them, name the vault
   item instead.
6. Commit only that file (plus `areas.md` if changed): `git add <files> && git commit -m "log: <title>"`, then
   `git push`. Report the path.

Skip trivial work: a typo fix doesn't need an entry.

## Set one up

1. Create a **private** repository (for example with `gh repo create <name> --private`).
2. Copy `examples/ops-notebook/` from the Canopy repository into it, adjust `areas.md` to the user's systems,
   commit and push.
3. Write `~/.config/canopy/ops-notebook.json`.
4. Existing logs elsewhere (a `maintenance-log/` folder, say): copy each into `entries/<YYYY>/`, add the
   frontmatter, keep the body, and add `migrated_from: <repo>/<path>`. Leave a pointer README where they
   were, rather than deleting them.

## Search it

`grep -ril '<term>' entries/` or by frontmatter: `grep -l 'areas:.*mail' -r entries/`. Answer with the entry
paths and dates, newest first.
