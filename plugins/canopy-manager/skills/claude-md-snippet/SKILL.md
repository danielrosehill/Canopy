---
name: claude-md-snippet
description: Offer to add Canopy's recommended snippet to the user's CLAUDE.md, reinforcing that every skill and tool is reachable through search. Use at the end of setup, or on "add the Canopy snippet", "update the snippet", or "remove the snippet".
---

# CLAUDE.md snippet

Optional. The connector already tells an agent how to search. The snippet repeats it in `CLAUDE.md`, which an agent
reads before anything else, for about 140 tokens per session.

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/skills/claude-md-snippet/scripts/snippet.py" --show` and show the user the text.
2. Ask whether to add it, and where: `~/.claude/CLAUDE.md` (the default, every project) or another file. Only write
   after a yes.
3. Run it with `--target <file>`. Add `--dry-run` first if the file already has content worth checking.

The block sits between `<!-- canopy:begin -->` and `<!-- canopy:end -->`. A rerun replaces only that block, and
`--remove` takes it out. Each write saves the old file as `<file>.bak-canopy`.

If the script refuses because of broken markers, show the user the lines it names and let them decide. Don't repair
them by hand: text between stray markers may be theirs.

On a client without this plugin, `catalogue__read` the script by its id and run a local copy.
