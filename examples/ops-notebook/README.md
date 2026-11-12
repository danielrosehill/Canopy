# Ops notebook

A log of updates and maintenance operations, kept as plain markdown in a **private** git repo. Agents write
entries with the `canopy-manager` skill `ops-log`; you read them in any editor, or open the folder as an
Obsidian vault. Nothing depends on Obsidian.

Copy this folder to a new private repository to start one (`ops-log init` does it for you).

## Layout

```
entries/YYYY/YYYY-MM-DD-<slug>.md   one entry per piece of work
templates/<type>.md                 the shape of each entry type
areas.md                            the area tags in use, one line each
```

## Entry types

| Type | For |
|---|---|
| `update` | Something was upgraded, added or changed on purpose |
| `maintenance` | Routine or corrective work: clean-ups, rotations, re-pairing, backups |
| `incident` | Something broke: what happened, cause, fix, how to tell next time |
| `decision` | A choice that changes how things are run, with the options rejected |

## Frontmatter

```yaml
---
date: 2026-11-12          # the day the work happened
type: maintenance         # update | maintenance | incident | decision
title: Short title
areas: [gateway]          # tags from areas.md; add new ones there
systems: []               # hosts, services, accounts touched (no secrets, no addresses)
repos: []                 # repo names, with commit hashes in the body
status: done              # done | partial | open
---
```

## Rules

- Write what landed and why, not a copy of `git log`. Unfinished work goes under **Open follow-ups**.
- No credentials, tokens, recovery codes or private addresses. The repo is private, but entries get pasted.
- One entry per piece of work. A follow-up that resolves an old item names the old entry's file.
- Obsidian: `.obsidian/workspace*.json` is ignored. With the obsidian-git community plugin set to auto-commit
  and push, edits made by hand reach the remote the same way agent entries do.
