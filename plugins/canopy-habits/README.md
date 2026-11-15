# Canopy habits

Your habits and SOPs, kept in your Canopy server instead of local files. Habits are rules ("verify the thing, not a signal
that stands in for it"); SOPs are procedures for a recurring job. Both are records in your Canopy context store
(`habit:user/<id>`, `sop:user/<id>`), written with the catalogue's `context_write`, so every device and agent gets them
through `catalogue__search`. One small generated block in your user-level CLAUDE.md or AGENTS.md carries a summary line per
habit and a trigger line per SOP; the full texts are read on demand.

Install it where you edit habits (a core Canopy plugin, alongside `canopy-connector`):

```
/plugin install canopy-habits@canopy
```

It needs the Canopy connection from `canopy-connector` and a server with context writes on (`CANOPY_CONTEXT_WRITE` not `false`).

## Skills

| Skill | Does |
| --- | --- |
| `capture-habit` | The entry point. A dictated rule in, the right record out: restates it, searches your records and the library, decides new or merge and the kind, proposes, writes, syncs |
| `remove-habit` | Retire (default) or delete a habit |
| `list-habits` | Your habits and SOPs, or which one covers something |
| `list-library` | The example habits in a library repo, marking what you have |
| `adopt-habit` | Take one, some or the whole library into your Canopy, values rendered |
| `share-habit` | Depersonalise one of yours into a library repo: a commit, or a PR if it is not yours |
| `sop-write` | Write an SOP from the session, or edit one, including re-dating after a clean run |
| `sop-remove` | Archive (default) or delete an SOP |
| `sop-list` | Your SOPs, staleness pass |
| `sop-run` | Match the situation to one SOP and follow it at its declared autonomy |
| `sync-habits` | Rebuild the CLAUDE.md block from the active records, with a 7,000-character budget |
| `check-habits` | Validate records and the block, audit summaries and overlap, reconcile hand-written prose |

`scripts/habits.py` (standard library only) renders and checks the block from records handed to it as JSON, so it needs no
network or credentials. Only `library`, `adopt` and `share-habit` reach a library, by shallow `git clone`.

## Habit libraries

The example habits are not part of this plugin. A library is a git repo with `library/habits/*.md` and an `index.json`; the
default is [`danielrosehill/Agent-Habits`](https://github.com/danielrosehill/Agent-Habits). Add others with
`CANOPY_HABITS_LIBRARIES=owner/repo[@ref],owner/other` (or `--repo` on one call). Library habits are written in the first
person with `{{VARIABLES}}` for tunable values; adopting renders them and records `library:` and `library_version:`.

## Where things go

[`references/habit-or-sop.md`](references/habit-or-sop.md) is the test: a rule is a habit (with a `trigger:` if it matters
for one kind of task), numbered steps are an SOP, a narrow trigger rule with no standing line is a `preference`, background
is `context`. Format of an SOP: [`references/sop-format.md`](references/sop-format.md).

## History

Folded in on 2026-11-15 from Agent-Habits v1.1.1 (itself ex Habits-Of-Claude and Claude-SOPs), which kept habits and SOPs as
files in a local folder. Dropped with that change: the local setup, install and sync skills, `sop.py` and the
`~/.claude-user-data` folder paths, now all Canopy records; the example library moved to stay in its own repo.
