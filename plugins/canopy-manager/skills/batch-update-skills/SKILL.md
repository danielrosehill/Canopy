---
name: batch-update-skills
description: Find every indexed skill, command, agent or reference that mentions something (a retired service, a renamed tool, a stale path or fact), fix it in each plugin's source repo, push, then sync Canopy and verify. Use on "update all the skills that mention X", "batch update skills", "find every skill that says X", or "these skills are stale".
---

# Batch update skills

When a fact changes (a service is retired, a tool is renamed, a path moves), many skills across many
plugins can go stale at once. This skill finds them through the catalogue and fixes them where they
come from.

**Propagate from source, never backward.** The catalogue is a copy of the source repos. Edit the plugin's
own repo, push it, then let Canopy re-index. Never edit an indexed copy, a marketplace cache or an
installed plugin directory: the next sync overwrites it, and the source stays wrong.

## 1. Find

1. **Search the catalogue** for the term and its variants (product name, container name, old path):
   `catalogue__search query="<term>" kind=skill full=true limit=50`. Repeat with `kind=command`,
   `kind=agent` and no `kind`. Search is hybrid (keyword and semantic), so a literal name scores well, but
   a hit is only a candidate.
2. **Confirm each hit literally.** `catalogue__read` the id and check that the text really says the stale
   thing. Read the skill's `references/` files too, by the ids in its `files` list.
3. **Sweep the source clones.** The index can lag a push, and it doesn't hold everything (`scripts/`, and
   docs in repos that aren't plugins). For each plugin found, take the `repo` field of its
   `plugin:<name>` record, `git pull` the local clone (or clone it), and grep the whole repo for the term.
   Grep sibling repos the user names as well.
4. **Sort the hits** before editing:
   - **stale:** a current-state statement that is now wrong. Fix it.
   - **historical:** a dated log entry or changelog line. Leave it.
   - **different thing:** the same word meaning something else (another host, another use). Leave it.
   - **generic option:** a public plugin offering the old thing as one choice among several. Leave it.

   Show the user this table (id, repo, file, line, class) when it is long or the classes are unclear.

## 2. Fix at the source

For each repo:
1. `git pull`.
2. Make minimal edits. Give the new fact with an absolute date ("X was retired on 2026-11-23; Y does
   this now"), so the next reader knows when it changed. Fix the instructions that depend on the fact
   too, such as a health check that inspects the retired container.
3. Bump the plugin's `version` in `.claude-plugin/plugin.json` the way the repo does it (patch for doc
   fixes), and add a CHANGELOG line if the repo keeps one.
4. Commit with a message that names the change, and push. Leave unrelated uncommitted work out.

## 3. Update the marketplace

If the plugin's marketplace entry pins a `version`, bump it to match, then commit and push the
marketplace repo. A plugin whose marketplace still shows the old version can be treated as unchanged.

## 4. Sync and verify

1. Sync each plugin: `canopy sync <plugin>` on the server, once per plugin. Agents can't call
   `catalogue__sync` through the gateway, which is a control-API tool. See `sync-and-embed`.
2. Poll `catalogue__health` until `sync_running` is false.
3. `catalogue__read plugin:<name>`: `head` must equal the commit you pushed.
4. `catalogue__read` one changed skill and check that the new text is there.
5. Run the step 1 searches again. Nothing classed **stale** should come back.
6. If embeddings are on, `canopy embed` shows the changed records as pending. Embedding is
   incremental and cheap for a handful of records, but follow `sync-and-embed` on cost.

## 5. Report

Per repo: files changed, commit, pushed, new version. Then the marketplace commit, the sync result,
and the verification. List what was deliberately left alone, and why. List stale material found
outside plugins (deployment docs, notebooks) and fix it the same way if the user wants it.

## Notes

- One commit per repo. Don't bundle unrelated plugins into one marketplace bump without naming each.
- A plugin whose source repo the server can't read (a private repo outside the token's reach) won't
  re-index. Check its `head` didn't move, and say so.
