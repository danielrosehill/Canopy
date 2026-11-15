# Habit or SOP?

Both are standing instructions you write once so an agent stops re-deriving them.
They differ in what they are and in how they cost context.

| | Habit | SOP |
|---|---|---|
| Is | A rule about how to behave | A procedure for one recurring job |
| Shape | A paragraph or two; no steps | Ordered `## Steps`, with a trigger |
| In force | Before the task is known (or, if it has a `trigger`, whenever that trigger applies) | Only once its trigger matches |
| Always in context | Its one-line `summary` | Its `trigger`, as one line |
| Read on demand | The full snippet and rationale | The whole procedure |
| Extra fields | `stance`, `summary`, optional `trigger`, `variables` (library) | `autonomy`, `last_verified`, `frequency`, `requires` |
| Lives in | Canopy (`habit:user/<id>`) | Canopy (`sop:user/<id>`) |

## The test: is it a rule or a sequence of steps?

The test is **rule versus steps, not always versus sometimes**:

- "Release tags are `v1.000`, `v1.001`…" is a rule, though it only matters when
  releasing. So it's a **habit with a trigger**. Its summary line sits in the
  "applies only when" group of the block.
- "To cut a release: bump the manifest, tag, push the tag, wait for CI, attach the
  .deb" is steps. That's an **SOP**. It can cite the habit rather than restate it.
- "Check the thing itself, not a signal standing in for it" is a rule that holds on
  every task. That's a plain **habit**.

A useful smell: if writing it down produces numbered steps, it is an SOP. If it
produces "always / never / prefer / when X, do Y", it is a habit.

## Neither

- **Facts about this machine or account** (which server fronts a tool, what a
  hostname means, which package manager to use) are environment configuration.
  They go in `CLAUDE.md` or reference files, not here. Test: would it still be
  true on someone else's machine? If not, it isn't a habit.
- **A capability** that needs tools or scripts to run is a skill.
- **A one-off preference** about the task at hand is neither. Just follow it.

## When one contains the other

An SOP may depend on a habit ("tag per `release-versioning`"). It should cite the
habit, never copy it, so the rule has a single home. A habit that keeps growing
steps should become an SOP, with the rule kept as a short habit if it applies more
widely than the procedure.
