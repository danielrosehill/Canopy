#!/usr/bin/env bash
# Capture what a fresh Claude Code session loads in DIR. No model call unless BILLED=1.
# Usage: launch-context.sh [DIR] [OUTDIR]
#   OUTDIR/interactive.txt  /context all from a real terminal session (the true total)
#   OUTDIR/headless.md      /context from `claude -p` (markdown tables, deferred tool sizes)
#   OUTDIR/launch.json      capture metadata
#   BILLED=1 adds billed first-turn tokens from one tiny print-mode call (BILLED_MODEL, default haiku)
set -uo pipefail
dir=$(cd "${1:-$PWD}" && pwd) || exit 1
out=${2:-$(mktemp -d)}; mkdir -p "$out"; out=$(cd "$out" && pwd)
command -v claude >/dev/null || { echo "claude not on PATH" >&2; exit 1; }
# A child of a running session must not inherit that session's markers.
unset_args=()
while IFS= read -r v; do unset_args+=(-u "$v"); done < <(compgen -e | grep -E '^(CLAUDECODE|CLAUDE_CODE_(ENTRYPOINT|SESSION_ID|SSE_PORT|CHILD_SESSION|EXECPATH)|CLAUDE_EFFORT|CLAUDE_PID)$')
cd "$dir" || exit 1
env "${unset_args[@]}" timeout 120 claude -p "/context" --no-session-persistence > "$out/headless.md" 2> "$out/headless.err" </dev/null
status=ok
if ! command -v tmux >/dev/null; then status="no-tmux"
else
  t() { env "${unset_args[@]}" tmux -L launch-context-$$ "$@"; }
  trap 't kill-server 2>/dev/null' EXIT
  t -f /dev/null new-session -d -s p -x 200 -y 600 -c "$dir" claude
  cap() { t capture-pane -p -t p; }
  for _ in $(seq 60); do
    sleep 1; s=$(cap)
    # Never answer the trust dialog: that decision is the user's.
    if grep -qiE 'do you trust|trust this folder' <<<"$s"; then status="trust-dialog"; break; fi
    grep -q '^❯' <<<"$s" && break
  done
  if [ "$status" = ok ]; then
    sleep "${MCP_SETTLE:-12}"          # MCP servers connect after the prompt appears
    t send-keys -t p -l '/context all'; sleep 1.5; t send-keys -t p Enter
    status=timeout
    for _ in $(seq 30); do sleep 1; s=$(cap); grep -q 'Free space' <<<"$s" && { status=ok; break; }; done
    sleep 2
  fi
  cap | sed 's/[[:space:]]*$//' | cat -s > "$out/interactive.txt"
fi
billed=null
if [ "${BILLED:-0}" = 1 ]; then
  billed=$(env "${unset_args[@]}" timeout 120 claude -p "reply ok" --model "${BILLED_MODEL:-haiku}" --no-session-persistence \
    --output-format json </dev/null 2>>"$out/headless.err" | python3 -Ic 'import json,sys
d=json.load(sys.stdin);u=d["usage"]
print(sum(u.get(k) or 0 for k in ("input_tokens","cache_creation_input_tokens","cache_read_input_tokens")) if d.get("num_turns")==1 else "null")' 2>/dev/null) || billed=null
fi
printf '{"dir":"%s","claude":"%s","captured":"%s","interactive":"%s","billed_first_turn":%s}\n' \
  "$dir" "$(claude --version)" "$(date -u +%FT%TZ)" "$status" "${billed:-null}" | tee "$out/launch.json"
echo "$out"
