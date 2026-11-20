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

## Status

Built and tested 2026-11-20 against a live gateway (Pipecat 1.16, Claude Haiku 5.5, Wyoming STT and TTS):
text and audio turns, held call then spoken yes, follow-ups, a bad token refused. Next: Pipecat's realtime
transports (WebRTC, WebSocket) for streaming clients, with on-server VAD and interruptions.
