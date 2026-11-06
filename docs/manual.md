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
| `canopy-manager` | On the server | Setup, diagnosis, upstreams, marketplaces, sync, backup, staging, CLIs |

On a client, add `canopy-manager` only while setting up, then remove it. Its skills stay findable by
search, because the server indexes them like any others.

## Validate and benchmark

Ask your agent to validate Canopy, or run `/canopy-connector:validate`. It is one skill because the two questions
are one: a connection only counts if an agent can retrieve through it, and retrieving tells you what it costs.

It reaches the gateway, lists its tools, then searches for and loads one tool and five skills sampled from your
own catalogue. The agent then does the same from its session, in its own words. You get the verdict, what it found,
how fast, and how many tokens.

| Verdict | Meaning |
| --- | --- |
| `GREEN` | Connected, and every probe was found |
| `AMBER` | Connected, but a probe missed, search fell back to keywords, or a step took over 2 seconds |
| `RED` | Unreachable, a core tool is missing, or the catalogue holds no skills |

The sampled probes take their words from each skill's description, so they prove retrieval works, not that it copes
with how you phrase things. To measure that, or to judge a change to search, ranking or descriptions, keep a file of
real phrasings and compare runs:

```bash
python3 plugins/canopy-connector/skills/validate/scripts/validate.py --queries queries.json --save before.json
python3 plugins/canopy-connector/skills/validate/scripts/validate.py --queries queries.json --compare before.json
```

`queries.json` is a list of `{"q": "plain words", "relevant": ["skill:plugin/name"]}`. Tokens are characters / 4, an
estimate that compares between runs. The script needs only Python, so it also checks a server for other clients.

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
