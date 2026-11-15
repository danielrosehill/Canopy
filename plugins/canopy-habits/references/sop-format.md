# The SOP format

One procedure, one Canopy record: kind `sop`, name `<id>` (record id `sop:user/<id>`),
written with `catalogue__context_write("sop", "<id>", text, description)`. The record name
*is* the id. `INDEX.md` and `README.md` are reserved and can never be ids.

Schema for the front matter: [`schema/sop.schema.json`](../schema/sop.schema.json).
Blank starting point: [`templates/sop.template.md`](../templates/sop.template.md).

## Front matter is the interface

Everything in the front matter is copied into `INDEX.md`; everything below it is
read only once that SOP has been chosen. This is the whole reason the format has
front matter at all — it is the difference between one table in context and
forty procedures in context.

```yaml
---
id: rotate-the-api-key          # kebab-case, equals the filename
title: Rotate the production API key
trigger: a key is due for rotation, or one has leaked
tags: [security, production]
autonomy: confirm
frequency: quarterly
last_verified: 2026-08-12
requires: [op, terraform]
---
```

| Field | Required | Notes |
| --- | --- | --- |
| `id` | yes | Kebab-case. Must match the filename. Changing it is a rename plus a reindex. |
| `title` | yes | The action, as a verb phrase. |
| `trigger` | yes | **The circumstance, not the action.** This is what the dispatcher matches. |
| `last_verified` | yes | ISO date the procedure was last seen to work end to end. |
| `tags` | no | Grouping only. |
| `autonomy` | no | `auto` \| `confirm` \| `confirm-each` \| `manual`. Defaults to `confirm`. |
| `frequency` | no | Calibrates staleness — a monthly SOP unverified in a year is suspect; a rare one is not. |
| `requires` | no | Tool, CLI or credential **names**. Never values. |
| `supersedes` | no | IDs this replaced, for the archive trail. |

### `trigger` is the field that earns its keep

The index is matched on it, so it decides whether a procedure is ever found
again. Write the situation:

- ✅ `a new plugin repo is ready to be shared publicly`
- ❌ `publishing a plugin` — restates the title and matches nothing the title didn't

Add the near-miss where one exists: `…ready to be shared publicly (not for
private plugins, which skip the marketplace entirely)`.

### `autonomy` is a permission, not a preference

| Value | Means |
| --- | --- |
| `auto` | Run it through. Reversible, or the user has said so explicitly. |
| `confirm` | One confirmation before the first step that changes something outside the machine. The default. |
| `confirm-each` | Ask at every mutating step. For procedures that touch money, other people, or production. |
| `manual` | Do not execute. Read it back and let the user drive. For anything requiring a human at a keyboard. |

If it is absent, treat it as `confirm`. An SOP never grants more latitude than
the user has already given for that class of action — it records a default, and
a default does not override a refusal in the session.

## Body sections

The template's headings are the expected ones. Keep them even when short; a
missing `## Steps` fails `sop.py validate`.

| Section | What belongs there |
| --- | --- |
| `## When this applies` | The trigger expanded, plus a case that looks similar and is not this one. |
| `## Preconditions` | What must be true before step 1, and how to check each. |
| `## Steps` | Ordered actions with exact commands. Say where order does not matter. |
| `## Verification` | How to tell from outside that it worked. "No errors" is not verification. |
| `## If it goes wrong` | Failures seen before, what each looked like, what fixed it. Irreversible steps named as such. |
| `## Notes` | Why this way and not the obvious way. Links out to fuller reference material. |

## What is not an SOP

The library rots fastest when it becomes a general notes folder. Keep out:

- **Reference material.** How a system is shaped is documentation; how *you* act
  on it is a procedure. Link to the former from `## Notes`.
- **One-offs.** If it will not happen again, it is a session, not a procedure.
- **Things the tooling already knows.** A procedure that is just `npm test` adds
  a lookup step and no information.
- **Preferences.** "Always use `uv`" is a standing instruction for `CLAUDE.md`,
  not a procedure with steps. Rule of thumb: if it has no order, it is not an SOP.

## The rule that keeps the library honest

`last_verified` is set when the procedure is **run and seen to work**, not when
the file is edited. A file edited today and last actually executed in January is
`last_verified: <January>`. Two dates that mean different things collapsing into
one is how a library ends up full of confident, wrong instructions.
