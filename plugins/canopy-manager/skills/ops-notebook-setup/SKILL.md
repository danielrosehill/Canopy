---
name: ops-notebook-setup
description: Create the user's own ops notebook from the public Ops-Notebook-Template and tailor it to their setup (areas, extra sections, host profiles), then import existing logs and docs from scattered repos. Use on "set up an ops notebook", "create my maintenance notebook", "customise the notebook", "add a section for X to the notebook", or "move these repo logs into the notebook". Writing single entries is the `ops-log` skill.
---

# Set up and tailor an ops notebook

The bootstrap is the public template `danielrosehill/Ops-Notebook-Template`. Its `README.md` defines the
layout, file types, frontmatter and rules; read it first. The user's notebook is a **private** copy that
grows its own areas and sections.

## Create

1. Ask for the repository name if not given. Create it private from the template:
   `gh repo create <owner>/<name> --private --template danielrosehill/Ops-Notebook-Template --clone`
   (no `gh`: create a private repo and copy the template's files in).
2. Write `~/.config/canopy/ops-notebook.json`: `{"path": "<local clone>", "remote": "<git remote>"}`.

## Tailor

Ask, in one round, what the notebook has to cover; propose answers from what you can see (repo names,
hosts in config, existing logs) rather than asking blind.

1. **Areas:** rewrite `areas.md` to the user's systems. Keep tags short and lowercase; one line each.
2. **Sections:** anything that isn't a host, network subject, runbook or reference (telephony, smart home,
   mail ops, a fleet of VPSs) gets a top-level folder with a short `README.md` saying what goes in it, and a
   line in the notebook README's layout block. Prefer an area tag over a folder when the material is just
   entries.
3. **Hosts:** one `hosts/<hostname>.md` per machine from `templates/host.md`, filled from what is known;
   leave unknown sections out and set `last_verified` to the day checked.
4. Commit as `setup: tailor areas and sections` and push.

## Import existing records

For each source repo the user names:

1. Classify every file: dated log → `entries/<YYYY>/`; machine or network state → `hosts/` or `network/`;
   procedure → `runbooks/`; research → `reference/`; scripts, config exports and machine-written
   JSON/JSONL → **not imported** (they stay in a code repo; report them).
2. Copy, add frontmatter, keep the body, and set `migrated_from: <repo>/<path>@<short-commit>`.
   Strip anything that is a credential. Merge, don't duplicate, when two sources describe the same state.
3. One commit per source repo: `import: <repo>`. Push.
4. List any skills, scripts or docs elsewhere that still point at the old paths, so they can be repointed.
   Don't delete or archive the source repo; that is the user's call.

## Add a section later

Same as Tailor step 2, one folder at a time, with the README layout block updated in the same commit.
