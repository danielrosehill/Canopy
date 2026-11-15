---
name: capture-habit
description: Turn a loosely dictated rule into the right Canopy record, new or merged into an existing one. Use on "add a habit", "new habit", "edit/update the X habit", "here's a habit", "from now on always/never ...", "my preference for X is ...", or when the user corrects you in a way that should persist. Restates the rule, checks existing records and the example library, picks the kind (habit, habit with a trigger, SOP, preference, context), shows a short proposal, and on a yes writes it and syncs the CLAUDE.md block.
---

# Capture a habit

Everything lives in Canopy: read and write only through the catalogue tools, never local files. The helper script is `$PLUGIN/scripts/habits.py` (standard library only; it never touches Canopy or the network except to clone a library). `$PLUGIN` is `${CLAUDE_PLUGIN_ROOT}` when set; otherwise `catalogue__read` the id `file:canopy-habits/scripts/habits.py`, save the text to a temp dir and run that. Records are `habit:user/<id>` and `sop:user/<id>`.

The user voice-types, so the rule arrives loose. Never call the wording garbled; restate it.

## 1. Restate it

One crisp rule line in the user's own words where they were sharp ("Verify the thing, not a signal that stands in
for it"). Address it to the agent. Keep the exception if they gave one; a rule with no carve-out gets over-applied.

## 2. Search before writing

- `catalogue__list(marketplace="context")` for habits, SOPs and preferences by id and description.
- `catalogue__search` the rule in plain words (kinds habit, sop, preference).
- `python3 "$PLUGIN/scripts/habits.py" library --json` for the example library. A match there: offer `adopt-habit`
  instead of writing a new one (it keeps `library:` so updates can be tracked).

Decide **new** or **edit**. A rule close to an existing habit is an edit or a merge: two overlapping habits drift
into two different rules. Name the record and say which way you went. `catalogue__read` the one you will change.

## 3. Pick the kind

Use [`references/habit-or-sop.md`](../../references/habit-or-sop.md). Rule versus steps:

| It is | Kind | Write with |
| --- | --- | --- |
| A rule that holds on every task | `habit`, no `trigger` | this skill |
| A rule that matters for one kind of task (releases, spending) | `habit` with `trigger:` | this skill |
| Numbered steps for a recurring job | `sop` | `sop-write` |
| A narrow rule with a trigger that needs no standing line ("repos about X go in org Y") | `preference` | `catalogue__context_write("preference", ...)`, one-sentence description, no block |
| Background (hosts, accounts, services) | `context` | as above |

Not a habit at all: a fact only true of this machine, or a one-off wish for the task at hand. Say so and stop.
A habit that keeps growing steps should become an SOP, with a short habit kept for the rule.

## 4. Fill in the record

Full file text, this frontmatter (the schema the block is built from):

```markdown
---
id: <name, equals the record name, lowercase-with-hyphens>
title: <short imperative title>
summary: "<the rule and its main exception, under 320 characters, no double quotes>"
kind: habit
trigger: "<circumstance, not action; omit for an always-on habit>"
stance: practice | prohibition
order: <multiple of 10; practices before prohibitions; look at neighbours with the list>
added: <YYYY-MM-DD>
revised: <YYYY-MM-DD, edits only>
status: active
source: "conversation, <YYYY-MM-DD>"
related: [<ids of related habits>]
---

## Snippet

<the full rule, addressed to the agent, standing alone>

## Why

<the reasoning and the cost of getting it wrong; on an edit add a dated line saying what changed>
```

`summary` is the only part every session sees: write the snippet first, then compress it. An edit keeps `added`,
sets `revised`, and rewrites `summary` whenever the snippet changed. Validate before showing:
`python3 "$PLUGIN/scripts/habits.py" validate <file> --kind habit --name <id>` (write the text to a temp file).

## 5. Propose, then write

Show only: new or edit, kind, id, the summary line, order and related ids; for an edit, a before/after of the
changed lines. Wait for a yes (a stated rule that is plainly one habit and the user said "add it" counts).

On yes: `catalogue__context_write("habit", "<id>", <full text>, <summary as description>)`, then `catalogue__read`
it back to verify. Then run `sync-habits` so the block carries the new line. Changes reach new sessions, not this one:
say so. If the habit is generic, offer `share-habit`.
