---
description: Load a skill from Canopy by id, or search for one in plain words.
argument-hint: <id or words>
---

Argument: $ARGUMENTS

If it is an exact id such as `skill:<plugin>/<name>`, call `catalogue__read` with that id, then
follow the skill.

Otherwise call `catalogue__search` with the words and show a short numbered list of ids with one
line each. When one is chosen, read it and follow it.
