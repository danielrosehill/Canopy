---
id: PLACEHOLDER_ID
title: PLACEHOLDER_TITLE
trigger: describe the situation in which this procedure applies
tags: []
autonomy: confirm
frequency: occasional
last_verified: PLACEHOLDER_DATE
requires: []
---

## When this applies

The circumstance that starts the procedure, and — just as usefully — a case
that looks similar but is *not* this. Most misfires are a near-miss match.

## Preconditions

What has to be true before step 1. Access, tools, a clean working tree, a
decision already taken. Say how to check each one, not just that it matters.

## Steps

1. First step. One action. Give the exact command where there is one.
2. Second step.
3. …

Keep the steps in the order they actually run. Where order does not matter, say
so — it saves the next run a needless serialisation.

## Verification

How to tell it worked, from outside the procedure. A URL that now resolves, a
row that now exists, an exit code. "No errors" is not verification.

## If it goes wrong

The failures seen before, what each one looked like, and what fixed it. Include
the ones that looked like something else. If a step is not reversible, say so
here and say what the fallback is.

## Notes

Why it is done this way rather than the obvious way. Anything discovered the
hard way. Links to fuller documentation elsewhere — this file is the procedure,
not the reference manual.
