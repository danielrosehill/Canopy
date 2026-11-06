---
name: validate
description: Validate a Canopy install and benchmark it: is the connection up, can an agent find and load a skill and a tool, how fast, for how many tokens. Run after installing. Use on "validate Canopy", "test the connection", "is the gateway up", "context benchmark", "how many tokens does a search cost", or "did that change help".
---

# Validate and benchmark

Run this after every install, on each device. Benchmarking is part of validating: a connection only counts when
retrieval works and you can say what it cost.

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/skills/validate/scripts/validate.py"`. It needs `CANOPY_MCP_URL`, and
   `CANOPY_TOKEN` if the server has auth. It finds and loads a tool and five skills sampled from this server.
   Add `--expect tool__one,tool__two` to find particular tools, or `--probes 0` for the connection alone.
2. Call `catalogue__health` from this session. The script proves the network path. This proves the session has
   the tools.
3. Retrieve for real, from this session. Take one skill and one tool from the catalogue, describe each task in your
   own words rather than its description's, then `catalogue__search` and `catalogue__read` what comes back.
4. Report in a few lines, leading with the script's last line: `GREEN`, `AMBER` or `RED`. Then what you searched
   for, what you found and at what rank, the latency, and the tokens for the search and the load. For example:
   "GREEN. Searched 'resize a batch of photos', found `skill:images/resize` first, 240 ms, about 180 tokens to find
   and 420 to load. The tool list costs about 2,000 per session."

On `RED`, say which step failed. If the script passes and this session has no tools, reconnect the server with `/mcp`.
`AMBER` on a missed probe means search is working but ranked that skill low or not at all: name the skill.

## Benchmark a change

Sampled queries come from each skill's own description, so they are easy, and they move when the catalogue does.
To judge search quality or a change, use a fixed file of real phrasings, with the exact right ids:

```json
[{ "q": "plain words a person would use", "relevant": ["skill:plugin/name"] }]
```

```bash
validate.py --queries queries.json --save before.json   # baseline
validate.py --queries queries.json --compare before.json # after the change
```

Add a query for every real miss, and never remove one to raise a score. Tokens are characters / 4, so compare runs
rather than quoting them as exact. On a server with reranking, each search costs a small fraction of a cent.
