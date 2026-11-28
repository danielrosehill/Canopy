# Hosted client tests: reach, retrieval, use

Shared by `test-chatgpt` and `test-claude-ai`. A hosted client passes when it can **reach** Canopy, **finds** the
right skill or tool for a plain request (retrieval accuracy), and **uses** it end to end, without being told which
app or tool to pick. Run the server baseline first: a client cannot do better than the front door does for its
profile, so a miss there is a Canopy problem, not a client one.

## 0. Server baseline

```bash
python3 plugins/canopy-manager/scripts/front_probe.py --url http://<front-bind>:8070/mcp \
  --token-file "$CANOPY_DATA/front-tokens/profile-<name>.token" --cases <cases.json> --use
```

`cases.json` holds the deployment's golden cases: a plain request, the record ids it must return in the top `k`,
and harmless `use` calls (read-only). Keep it in the deployment's private config, and add a case whenever a client
misses something. Fix any failing case here first (`diagnose`, `hosted-client-checks.md` step 2): the usual causes
are a tool missing from the catalogue's `TOOLS_GROUP` and a profile that does not reach the server.

## 1. Reach

In a new chat (ChatGPT: a temporary chat; claude.ai: a new chat with the connector enabled):

> Connectivity test, change nothing: list the tools you can see from <app>.

Pass: the five front-door tools (prefixed by the portal server's id) and nothing that contradicts the profile.
The front door log shows the session (`canopy.front call ...` lines from Canopy fd18513).

## 2. Retrieval accuracy

For each golden case, in the same chat:

> Search Canopy for "<query>" and give me the ids of the top results. Do not run anything.

Score it like the probe: the expected ids in the top `k`. Compare with the baseline. A case the baseline passes
and the client fails is the client reformulating the query; note the query it actually sent (front door log,
`target=`), and if the client consistently rewrites queries badly, add the rewritten form as a golden case too.

## 3. Use, without being told

The test that matters: a **plain** request with no app named, in a **new** chat, as a person would type it.

> Please print a label that says "<client> test".

Pass: the client picks the Canopy app by itself, searches, calls the tool, and the target system confirms it (the
print hub's job list, the chore list, ...), not only the client's reply. Real actions only with the user's go-ahead
(a test label is the usual one); otherwise use a read-only request ("which printers do we have?").

Record per test: the client and model, time (UTC), pass or fail, seconds, and for a fail the hop it failed at.
If the client used another app or answered without calling Canopy, see the client's `debug-*` skill, step "the
chat uses that app": too many competing apps, or a name and description that do not say what Canopy is for.
