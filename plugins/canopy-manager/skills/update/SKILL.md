---
name: update
description: Update a running Canopy server to the newest release, or pin it to one. Use on "update Canopy", "upgrade the stack", "pull the latest version", "redeploy", or "what version am I on".
---

# Update Canopy

Run these on the host, from the Canopy checkout.

1. See where it is now: `canopy status`, and `git describe --tags` in the checkout.
2. Read the release notes on GitHub for every release since then. A database migration cannot be undone by
   going back, so tell the user first if one is listed.
3. Update:

   ```bash
   clients/canopy update
   ```

4. Read the last line:

   | Line | Means |
   | --- | --- |
   | `updated on v1 at <commit>` | Done. Run the connector's `validate` skill. |
   | `already up to date` | Nothing changed. |
   | `went back to the previous version` | The new version was unhealthy, and the old one is running. Use `diagnose`, with `canopy compose logs --tail 50`. |
   | `not touched` | A pull or fetch failed: check the network and retry. |
   | `has local changes` | Someone edited the checkout. Move that work to agent-data (`docs/customising.md`), then run it again. |
   | `STILL NOT HEALTHY` | Going back did not help. Use `diagnose`, and read `canopy status`. |

`CANOPY_TAG` in `agent-data/secrets.env` says how far it may move: `v1` follows every v1 release, `v1.00` stays
there, `main` is every commit. Moving to a new major such as `v2` is a choice: read its notes, change the tag, and
run `update`.

A deployment with its own wrapper, for example one that attaches extra networks after start, should run its own
script instead, because `update` knows only `compose.yaml` and the override file.
