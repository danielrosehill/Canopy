---
name: check-launch-context
description: Check what a Canopy client loads at launch, suggest ways to minimise it, and apply only those that don't break Canopy. Use on "check launch context", "trim my context", "what loads at startup".
---

# Check launch context on a Canopy client

Measure what a fresh Claude Code session loads, suggest how to cut it, and apply what the user approves. This is the
Canopy version of `check-launch-context` in [Context-Mgmt](https://github.com/danielrosehill/Context-Mgmt). On a
client connected to Canopy, the rules here win: a trim that breaks search costs more than it saves.

Run it on the client, not the server. It needs `claude` and `tmux` there.

## 1. Baseline

1. Run the connector's `validate` and keep its result. If it isn't `GREEN`, fix that first (`diagnose`).
2. `BILLED=1 "${CLAUDE_PLUGIN_ROOT}/skills/check-launch-context/scripts/launch-context.sh" <dir> <outdir>`

On a client without this plugin, `catalogue__read` the script by its id and run a local copy.

It opens a new session in tmux, runs `/context all`, and closes it. `interactive.txt` has the real categories,
`headless.md` the per-item sizes. `BILLED=1` makes one tiny Haiku call and adds `billed_first_turn` to `launch.json`.

Deferred tool schemas are not loaded, so never count them as savings. `/context` undercounts and can hide a saving
(disabling skills left its total flat while billed tokens fell), so judge a change on `billed_first_turn`.

## 2. Never trim these

| Keep | Why |
|---|---|
| The Canopy MCP server, connected at user scope in every project, and its server instructions | Everything else is reached through it |
| `ToolSearch`, and `ENABLE_TOOL_SEARCH` not set to `false` | Without it every tool schema loads at launch |
| No `permissions.deny` rule matching the Canopy server's tools, including `mcp__*` | It removes the tools |
| `canopy-connector` and its prompt hook | It finds skills, and prints only on a confident match |
| Text inside `canopy:begin` and `agent-habits:begin` blocks | Generated. Change the source, then use `claude-md-snippet` or `sync-habits` |
| Rules that apply in every session | They have nothing to search on |

Canopy is the source of truth for its own behaviour. Don't edit its tool groups or records from a client to save
context. Tools and upstreams go through `manage-upstreams`, and other settings through `docs/customising.md`.

## 3. Suggest remediation

Always follow the measurement with suggestions: item, tokens, fix, estimated saving, what is lost. Largest first, then
the few you recommend.

| Surface | Fix on a Canopy client |
|---|---|
| CLAUDE.md rules with a trigger, and background notes | `migrate-context`, not local side files. The other devices get them too |
| Path-specific rules | `.claude/rules/` with `paths:` |
| Installed plugins | If the plugin is in the catalogue, stop installing it. First `catalogue__search` its job in plain words and confirm its skills come back. Keep any whose hooks, commands or agents must be loaded |
| `canopy-manager`, `canopy-habits` at user level | Remove after setup: both are found by search. Check that nothing local, such as a hook, calls them by path |
| Local MCP servers | If it can run on the server, register it with `manage-upstreams`, confirm a call works, then remove the local one. Local browser and desktop tools stay |
| Local skills and agents | Publish to an indexed marketplace, confirm by search, then remove. Single-project ones move to that project |
| The snippet | About 140 tokens, and it repeats the connector. `claude-md-snippet --remove` if search is used reliably without it |
| Auto-memory index | `migrate-memory` |
| Built-in skills | `skillOverrides`, or `disableBundledSkills` |
| SessionStart hooks | Print only on a problem |
| Git instructions | `includeGitInstructions: false` if your own rules cover commits |

Anything in the catalogue must be confirmed by search before its local copy goes. Being listed isn't enough.

## 4. Apply and re-measure

Apply only rows the user picks. One at a time:

1. Back up the file, then make the change.
2. Repeat the step 1 capture and compare `billed_first_turn` in the same folder.
3. Run `validate` again, and `catalogue__search` for the thing removed, from a new session.
4. Revert if `validate` isn't still `GREEN`, the search misses, or tokens didn't drop.

Report before and after, what was applied, and what was left because Canopy needs it.
