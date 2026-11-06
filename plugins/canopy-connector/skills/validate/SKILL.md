---
name: validate
description: Validate a Canopy install and benchmark it: is the connection up, can an agent find and load a skill and a tool, including one you know you have, how fast, for how many tokens. Run after installing. Use on "validate Canopy", "test the connection", "is the gateway up", "context benchmark", "how many tokens does a search cost", "can it find my skill", or "did that change help".
---

# Validate and benchmark

Run this after every install, on each device. Benchmarking is part of validating: a connection only counts when
retrieval works and you can say what it cost.

1. Make sure a known skill is on file. If `~/.config/canopy/known-skills.json` is missing, ask the user to name a skill
   they know they have and how they would ask for it, then save it as below. One of their own beats a stock one, and
   one from a private plugin also proves private plugins are indexed. If they have none yet, carry on without.
2. Run `python3 "${CLAUDE_PLUGIN_ROOT}/skills/validate/scripts/validate.py"`. It needs `CANOPY_MCP_URL`, and
   `CANOPY_TOKEN` if the server has auth. It finds and loads a tool, five skills sampled from this server, and the
   known skills. Add `--expect tool__one,tool__two` to find particular tools, or `--probes 0` for the connection alone.
3. Call `catalogue__health` from this session. The script proves the network path. This proves the session has
   the tools.
4. Retrieve for real, from this session. Take one skill and one tool from the catalogue, describe each task in your
   own words rather than its description's, then `catalogue__search` and `catalogue__read` what comes back.
5. Report in a few lines, leading with the script's last line: `GREEN`, `AMBER` or `RED`. Then what you searched
   for, what you found and at what rank, the latency, and the tokens for the search and the load. For example:
   "GREEN. Searched 'resize a batch of photos', found `skill:images/resize` first, 240 ms, about 180 tokens to find
   and 420 to load. The tool list costs about 2,000 per session with full schemas, far less on a client that defers them."

On `RED`, say which step failed. If the script passes and this session has no tools, reconnect the server with `/mcp`.
`AMBER` on a missed probe means search is working but ranked that skill low or not at all: name the skill.

## Known skills

Sampled probes prove retrieval works. A known skill proves it finds what this user would actually ask for, so the
accuracy is measured rather than hoped for. The file is a list, and stays on this device because it may name private
skills:

```json
[{ "q": "how the user would really ask", "relevant": ["skill:plugin/name"], "close": ["skill:plugin/sibling"] }]
```

Write `q` in the user's words, never the skill's description. Confirm the id with `catalogue__read`. `close` is
optional: records that would also do the job. Choose them from the catalogue before the first run, not from what
search returned.

| Grade | Meaning | Report |
| --- | --- | --- |
| `first` | Ranked first | Nothing more |
| `behind-close` | Below only its own plugin or a `close` record | Which one, and that it is acceptable |
| `outranked` | Below something else | Read each record it names and say if an agent picking it would do the job |
| `buried` | Indexed, but past the results an agent sees | The deep rank: search needs work, or the description does |
| `missed` | Indexed, not in the top 50 | The phrasing shares nothing with the skill: fix the description |
| `not-indexed` | Not in the catalogue | A stale id or a sync problem, not a ranking one: run `catalogue__health` |

If an `outranked` record really would do the job, add it to `close` and say so. If it would not, that is the finding.

## Benchmark a change

Sampled queries come from each skill's own description, so they are easy, and they move when the catalogue does.
To judge search quality or a change, use a fixed file of real phrasings, with the exact right ids:

```json
[{ "q": "plain words a person would use", "relevant": ["skill:plugin/name"], "close": [] }]
```

```bash
validate.py --queries queries.json --save before.json   # baseline
validate.py --queries queries.json --compare before.json # after the change
```

The known skills file works here too: pass it as `--queries`, so the sample doesn't move the numbers. `--compare`
lists each case whose rank or grade changed.

Add a query for every real miss, and never remove one to raise a score. Tokens are characters / 4, so compare runs
rather than quoting them as exact. On a server with reranking, each search costs a small fraction of a cent.
