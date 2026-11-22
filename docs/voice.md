# Voice

`canopy-voice` is a voice front end for Canopy tool groups, built on [Pipecat](https://github.com/pipecat-ai/pipecat).
Wake word detection stays on each device. A device is a thin client: it sends what was said, and plays the
reply. The service is the `voice` compose profile, image `danielrosehill/canopy:voice-<tag>`, source `voice/`.

```
 device (wake word on device)  --bearer token-->  canopy-voice  --MCP-->  gateway group for that speaker
                                                   | STT, TTS (Wyoming or any Pipecat service)
                                                   | Claude tool loop (Pipecat, Anthropic)
                                                   | confirmation gate  <--  Canopy group policy
```

## What it does

- **One group per speaker.** The token a device sends says who is speaking, and each speaker names a gateway
  group. What a person can do is that group, so adding a capability is a Canopy config change, not a code change.
- **Confirmation in code.** The group's policy (`GET /v1/gateway/groups/<name>/policy`, see
  [customising](customising.md#confirmation-before-a-tool-call)) marks which tools need a yes. When the model
  calls one, the call is held and the model is told to read it back and ask. The held call runs only if the
  speaker's next utterance is a yes, judged from the transcript by word lists (`confirm.yes`, `confirm.no`), not
  by the model, and only with the same arguments. Anything else drops it. If the policy cannot be read, every
  tool needs a yes.
- **Follow-ups.** A reply carries `conversation_id`. Sent back within `session_ttl_s`, it continues the
  conversation, so a follow-up need not repeat what it refers to.

## API

All but `/health` take `Authorization: Bearer <speaker token>`.

| Call | Body | Returns |
|---|---|---|
| `GET /health` | | `ok`, and per group: tool count, how many need confirmation, where the policy came from |
| `POST /v1/turn` | JSON `text`, `conversation_id`?, `speak`? | JSON `reply`, `conversation_id`, `speaker`, `tool_calls`, `held`, `errors`, `seconds`, and `audio_b64` (WAV) if `speak` |
| `POST /v1/turn/audio` | multipart `audio` (anything ffmpeg reads), `conversation_id`?, `reply=wav\|json` | WAV, with headers `X-Conversation-Id`, `X-Transcript`, `X-Reply-Text` (URL-encoded); or the JSON above plus `transcript` |

`held` is the action waiting for a yes, if any (`tool {json arguments}`). A client may show it.

## Set it up

1. Make a group per speaker in `config/servers.json`, with `confirm` and `no_confirm` lists where the
   annotations are not enough. Keep the toolbox meta-tools out of voice groups: `toolbox__call_tool` reaches
   every tool, and its arguments say little a listener could confirm.
2. Copy `examples/overlay/config/voice.yaml` and `voice/prompt.md` into your overlay's `config/`, and edit them.
3. In `secrets.env`: `ANTHROPIC_API_KEY`, one `VOICE_TOKEN_<NAME>` per speaker, `CANOPY_POLICY_TOKEN`, and
   in enterprise mode `VOICE_GATEWAY_TOKEN`. Add `voice` to `COMPOSE_PROFILES`.
4. `clients/canopy up`, then `curl -H "Authorization: Bearer $VOICE_TOKEN_ALEX" localhost:8797/v1/turn -d '{"text":"hello"}'`.

From a checkout, without Docker: `cd voice && uv run canopy-voice --config <voice.yaml> tools alex` lists a
speaker's tools and which need a yes; `ask alex "text" "follow-up"` runs a conversation from the command line.

## Wide groups: Canopy tool discovery

A speaker's group can hold the whole surface their accounts reach. Tools are found the way every Canopy client
finds them: the group's catalogue search ranks them (hybrid keyword and vector search, then the reranker; see
[architecture](architecture.md#search)), and the model calls what it found. Only the everyday tools are sent on
every turn.

```yaml
tool_discovery:
  search: catalogue__search                                  # the group's Canopy catalogue search tool
  loaded: ["home__*", "calendar__get_events", "mail__send"]  # fnmatch patterns sent natively on every turn
speakers:
  sam:
    group: voice_sam
    tool_discovery: {search: household__search}              # a scoped catalogue's search, for this speaker
```

What the model gets: the loaded tools, the search tool, and a built-in `call_tool(name, arguments)`, the voice
twin of `toolbox__call_tool`. canopy-voice rewrites each search result before the model reads it:

- A tool outside the speaker's group is dropped, and `call_tool` refuses one too. The group stays the allow-list,
  so build it as a deny-list (`included_servers` with `excluded_tools`) and the search can't reach past it.
- A found tool that isn't loaded gets `call: call_tool` and its `input_schema` (from the group's own tools/list),
  so the model calls it in the next step instead of reading the tool first. A loaded one gets its own name.
- The confirmation gate judges the real tool: `call_tool` for `mail__send` is held and keyed as `mail__send` with
  its arguments, and runs after a yes exactly like a native call. That is why `toolbox__call_tool` itself stays out
  of the group: it would carry every tool under one name.

Say in the prompt that more tools exist and to search the catalogue before saying it can't do something: without
such a rule Haiku answered "I have no currency feed" with an exchange-rate tool one search away. A search tool
missing from the group is logged at start, and that speaker then reaches only the loaded tools.

### Claude's tool search (legacy)

`tool_search` is the older way, kept for groups with no Canopy catalogue: every tool's schema is sent, deferred,
and Claude's own BM25 search picks from them. It bypasses Canopy's ranking, so use `tool_discovery` where a
catalogue exists. The two are exclusive.


Every tool's schema goes to the model on every turn, so a group of a few hundred tools is slow and costly. Turn on
Claude's [tool search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool) in
`voice.yaml`:

```yaml
tool_search:
  loaded: ["home__*", "calendar__get_events", "mail__send"]   # fnmatch patterns kept loaded on every turn
```

Every other tool is sent with `defer_loading: true`, and the BM25 search tool goes first; Claude searches, then
calls what it found. A deferred tool costs no context until it is loaded, and the prompt cache still holds.
Say in the prompt that more tools exist and to search before saying it can't do something: without that rule
Haiku answered "I have no currency feed" with an exchange-rate tool one search away.

- The confirmation gate works as before: a found tool is a real tool name, judged by the group policy. Build a
  wide group as a deny-list (`included_servers` with `excluded_tools`), and let annotations decide what needs a
  yes; a tool with no `readOnlyHint` does.
- Keep `toolbox__call_tool` out: it would bypass the gate by name.
- How it fits Pipecat: the search runs on Anthropic's side, so no handler sees it, and Pipecat keeps neither its
  blocks nor (here) the thinking blocks in the context it sends back. The API accepts that (a follow-up with only
  `tool_use` + `tool_result` succeeds, and the next turn calls a found tool again without searching), but not a
  thinking block left without its neighbours, so `DeferringAdapter` drops assistant thinking when tool search is on.
- Supported models: Claude Haiku 4.5 and later, Sonnet 4.5+, Opus 4.5+ (up to 10,000 deferred tools a request).

## Speech

`stt` and `tts` take `provider: wyoming` with a `uri` (`tcp://host:port`): the protocol Home Assistant's
speech servers speak (faster-whisper, Piper, Kokoro and others). Any other Pipecat service is named by class
path, with `api_key_env` for its key and `settings` for its Settings:

```yaml
tts: {provider: "pipecat.services.openai.tts:OpenAITTSService", api_key_env: OPENAI_API_KEY,
      settings: {voice: alloy}}
```

A provider outside the image needs its package installed: build on `danielrosehill/canopy:voice-<tag>`.

## Customising

Everything personal lives in the overlay: speakers, tokens, groups, the prompt, the speech servers. A
speaker's `context` is added to their per-turn line, which also gives the local time, so the system prompt
stays the same between turns and is served from the prompt cache.

## Timers and reminders

"Set a timer for ten minutes" and "remind me at six to call the plumber" are kept by canopy-voice itself and fire
at their time, wherever the speaker is. No calendar event, no satellite device, no change to the client: a device
only posts turns and never holds a connection, so the server cannot ring it directly. Instead each timer, when
due, runs the speaker's **delivery calls**: gateway tools in their own group that reach them (a phone push, a
message spoken on their desktop). Which tools is config.

```yaml
timers:
  store: /data/timers.json      # required; without it the tools are off. /data = $CANOPY_DATA/voice
  deliver:                      # default for every speaker: one or more {tool, arguments}
    - tool: notify__push
      arguments: {to: "{speaker}", message: "{message}"}
  # max_seconds: 604800         # furthest ahead a timer may be (7 days)
  # late_grace_s: 3600          # a timer found overdue by more than this after downtime is dropped, not sent
  # poll_s: 15                  # how often the file is re-read (timers added by another process)
  # messages: {timer: "Timer done: {label}", timer_unlabelled: "Your {duration} timer is done",
  #            reminder: "Reminder: {text}"}
speakers:
  sam:
    timers: {deliver: [{tool: notify-sam__push, arguments: {message: "{message}"}}]}   # replaces the default
```

In argument strings `{speaker}` (id), `{name}`, `{address}`, `{message}` (from `messages`), `{label}` / `{text}`,
`{duration}` (as spoken, "10 minutes"), `{kind}` and `{id}` are filled in; any other `{...}` is left as is.

- **Tools** (built in, offered only to a speaker with at least one delivery call, never deferred by tool
  search, no confirmation): `set_timer(seconds, label?)`, `set_reminder(at, text)` with `at` local
  `"HH:MM"` (next occurrence) or `"YYYY-MM-DD HH:MM"`, `list_timers()`, `cancel_timer(id? | label? | all?)`
  (with one running and nothing named, that one). Each speaker sees and cancels only their own.
- **Delivery** calls the tool through the speaker's group connection, outside a turn and outside the
  confirmation gate: the operator chose the call, the model did not. It counts as delivered if any call
  succeeded. A tool not in the group is a warning at start-up. Logged as `timer <id> for <speaker> fired|failed
  (...)`, and kept in the file's `history` (last 50) with the tool's reply.
- **Restarts.** The file is the source of truth. A timer that came due while the service was down fires on
  start if it is late by less than `late_grace_s`, else it is recorded `missed`.
- **One firing process.** Only `serve` runs the timer loop; `canopy-voice ask` can set timers (the server
  picks them up within `poll_s`) but never fires them.
- Precision is about a second for timers set through the server (its loop wakes on each new timer).
- Tell the prompt to use these for countdowns and reminders within the next days, and the calendar for
  appointments, or "remind me in twenty minutes" may still become a calendar event.

## Turn log

`turn_log: /data/turns.jsonl` in `voice.yaml` appends one JSON line per turn (off when unset). `/data` is
`$CANOPY_DATA/voice` on the host (runtime state, not the overlay). A record has `ts` (local, ISO), `client` (the `client` field of a `/v1/turn` body,
`audio` for `/v1/turn/audio`, else empty), `model`, `text` (what was heard or typed), `gate_note` (the note added when a
held call was waiting for a yes), and the reply fields: `reply`, `conversation_id`, `speaker`, `tool_calls` (tool,
arguments, result cut to 500 characters), `held`, `errors`, `seconds`. Server-side tool-search blocks are not in it.
It is for reviewing failures and tuning the prompt; nothing rotates it. Clients that replay turns for testing should
send a `client` value so a review can leave those turns out.

## Status

Built and tested 2026-11-20 against a live gateway (Pipecat 1.16, Claude Haiku 5.5, Wyoming STT and TTS):
text and audio turns, held call then spoken yes, follow-ups, a bad token refused. Next: Pipecat's realtime
transports (WebRTC, WebSocket) for streaming clients, with on-server VAD and interruptions.
