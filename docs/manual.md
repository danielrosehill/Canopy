# User manual

Canopy keeps your skills on a server and lets an agent find one when it needs it, instead of loading
them all into every session. This page covers connecting a client and, above all, how finding works.

## Install

Set up the server first: see the [README](../README.md). Then install one plugin, at user level, on each
device:

```
/plugin marketplace add danielrosehill/Canopy
/plugin install canopy-connector@canopy
```

Set `CANOPY_MCP_URL` to your server's MCP address, and `CANOPY_TOKEN` to your client token (not needed
in development mode). Then [validate it](#validate-and-benchmark), on every device you connect.

**Two plugins, on purpose.** Whatever is installed at user level costs context in every session.

| Plugin | Where | Holds |
| --- | --- | --- |
| `canopy-connector` | User level, every device | The connection, the `find` and `validate` skills, `/skill` and the prompt hook |
| `canopy-manager` | On the server | Setup, diagnosis, upstreams, marketplaces, sync, backup, staging, CLIs, the CLAUDE.md snippet |

On a client, add `canopy-manager` only while setting up, then remove it. Its skills stay findable by
search, because the server indexes them like any others.

## Update

```bash
canopy update
```

This moves to the newest release your `CANOPY_TAG` allows, and goes back to the previous images if the new
ones come up unhealthy. If a backup target is set and there is something new, it backs up first, and stops if that
fails: going back cannot undo a database migration, but `canopy restore --replace` can, from that backup.
`--no-backup` skips it. Your folder and data are not touched. Tags and how it works:
[Updating](customising.md#updating). Keep your own changes in the agent-data folder, because `update` stops if
the checkout has local edits.

## Validate and benchmark

Ask your agent to validate Canopy, or run `/canopy-connector:validate`. It is one skill because the two questions
are one: a connection only counts if an agent can retrieve through it, and retrieving tells you what it costs.

It reaches the gateway, lists its tools, then searches for and loads one tool, five skills sampled from your own
catalogue, and any skill you have told it you know exists. The agent then does the same from its session, in its own words. You get the verdict, what it found,
how fast, and how many tokens.

| Verdict | Meaning |
| --- | --- |
| `GREEN` | Connected, and every probe was found |
| `AMBER` | Connected, but a probe missed, a known skill ranked below something unexpected, search fell back to keywords, or a step took over 2 seconds |
| `RED` | Unreachable, a core tool is missing, or the catalogue holds no skills |

The sampled probes take their words from each skill's description, so they prove retrieval works, not that it copes
with how you phrase things.

### Check a skill you know you have

So name one. The first time you validate, the agent asks for a skill you know exists and how you would ask for it,
and saves that in `~/.config/canopy/known-skills.json`. The file stays on your device, so it can name private skills,
and a private one is the better test: it also proves your private plugins are indexed.

```json
[{ "q": "how you would really ask", "relevant": ["skill:plugin/name"], "close": ["skill:plugin/sibling"] }]
```

Every run then sends your words to the server as an agent would, and grades what comes back. `close` lists records
that would also do the job: when several skills are near matches, one of those ranking higher is fine, and anything
else is named so the agent can judge whether it was relevant.

| Grade | Meaning |
| --- | --- |
| `first` | Ranked first |
| `behind-close` | Below only its own plugin or records listed in `close` |
| `outranked` | Below something else, named in the output. This alone makes the run `AMBER` |
| `buried` | Indexed, but past the results an agent sees, found by searching deeper |
| `missed` | Indexed, but not in the top 50 either |
| `not-indexed` | Not in the catalogue, so the id is stale or the sync missed it |

### Compare runs

To judge a change to search, ranking or descriptions, keep a file of real phrasings and compare runs. Your known
skills file works for this:

```bash
python3 plugins/canopy-connector/skills/validate/scripts/validate.py --queries queries.json --save before.json
python3 plugins/canopy-connector/skills/validate/scripts/validate.py --queries queries.json --compare before.json
```

`queries.json` has the same format as the known skills file, and replaces the sample so the numbers hold still.
`--compare` shows what moved in the totals, and each case whose rank or grade changed. Tokens are characters / 4, an
estimate that compares between runs. The tool list is reported as full schemas and as names only, because a client
that defers schemas, as Claude Code does, loads little more than the names at the start of a session. The script needs only Python, so it also checks a server for other clients.

## Reinforce it in CLAUDE.md

The connector already tells an agent how to search. If you want that in `CLAUDE.md` too, which an agent reads first,
the `claude-md-snippet` skill in `canopy-manager` shows a short recommended block and adds it if you agree. It costs
about 140 tokens per session, sits between markers, and can be updated or removed. Setup offers it.

## How finding skills works

Skills are not installed. They live in the catalogue, and an agent reaches them in three steps.

**1. Search.** `catalogue__search` takes the task in plain words, in any language the embedding model
handles. It combines keyword and semantic ranking. `results` holds skills, commands, agents and plugins,
and `tools` holds matching gateway tools, ranked separately so a large tool set cannot crowd skills out.

**2. Read.** `catalogue__read` with an id returns the whole skill. A skill lists its other files, which
are fetched only if it asks for them, so a long reference costs nothing until it is needed.

**3. Follow it,** or call the tool. A tool not in the agent's list goes through
`toolbox__call_tool(name, arguments)`. `catalogue__read` on a `tool:` id gives its schema.

### Reading a search reply

| Field | Means |
| --- | --- |
| `confidence` | Cosine similarity of the best result, 0 to 1. Absent when search is keyword only |
| `weak: true` | Nothing strong matched. Stop looking and do the task directly |
| `degraded` | Part of search is off, usually semantic. A miss proves less: try other words |

`weak` is a signal, not a filter: the results are still there. The line is `SEARCH_CONFIDENCE_MIN`,
0.6 by default. That value suits `bge-m3`, and another embedding model needs its own.

### The prompt hook

Agents told to search first still often do not. So on each prompt of three words or more, the connector
searches for you. If the reply has a `confidence` and is not `weak`, it adds up to 3 ids to the
agent's context, with one short line each. Otherwise it adds nothing, so most prompts cost no tokens.

It skips slash commands, stops after 2.5 seconds, and stays silent on any failure. Your prompt's first
500 characters go to your own server. Set `CANOPY_HOOK=off` to turn it off.

### By hand

`/skill <id>` loads a skill by id, and `/skill <words>` searches. `clients/canopy-pick` is a terminal
picker for people.

## Other clients

A host that supports the MCP Skills extension can list and read skills at `POST /v1/skills/mcp` on the
API, with the API token. The gateway cannot carry these methods, and the extension has no search, so
`catalogue__search` remains the way to find one. Hosted clients: [saas-and-web-clients.md](connect/saas-and-web-clients.md).
All API routes: [api.md](api.md).
