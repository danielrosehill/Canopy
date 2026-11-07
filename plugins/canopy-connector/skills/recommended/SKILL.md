---
name: recommended
description: Show the plugins recommended for an agent working on your local network as a checklist, and install the ones the user picks. Use on "recommended plugins", "what plugins should I add", "install plugins for Canopy", or "plugins for Home Assistant, Cloudflare or my network".
---

# Recommended plugins

The list is the `canopy-recommended` marketplace, in the public repo `danielrosehill/Canopy-Recommended`.

1. Read the list:
   `curl -fsSL https://raw.githubusercontent.com/danielrosehill/Canopy-Recommended/main/.claude-plugin/marketplace.json`
2. Run `claude plugin list` and mark what is already installed.
3. Show a numbered checklist grouped by `category`: name and a one-line description. Mark any plugin tagged
   `third-party` (written by someone else), `runs-hooks` (runs code automatically during sessions) or `connects-mcp`
   (adds an MCP server). If the user
   asked about one subject, show that category first. Ask which numbers they want. Install nothing they did not pick.
4. Install the picks:
   ```
   claude plugin marketplace add danielrosehill/Canopy-Recommended
   claude plugin install <name>@canopy-recommended --scope user
   ```
   Use `--scope project` if they want a plugin in this project only. If the marketplace is already added, go on to
   the install. Run one install per plugin and carry on past a failure.
5. Report what installed and what failed, with the error. New plugins load in a new session, or after `/reload-plugins`.

If the user wants the plugins searchable on their Canopy server rather than installed on this machine, that is one
step for the whole list: `canopy add-marketplace danielrosehill/Canopy-Recommended`, then the `sync-and-embed` skill
in `canopy-manager`. Plugin names are global on a server, so check that no marketplace it already indexes lists the
same plugins.
