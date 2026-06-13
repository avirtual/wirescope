#!/usr/bin/env bash
# Launch wirescope fully DETACHED from the calling shell / CLI session.
#
# Why: a Claude Code `run_in_background` job is a child of the CLI process and
# is signaled (SIGTERM/SIGHUP) when the CLI exits — the proxy dies with the
# session. We instead nohup + disown inside a subshell so the launching shell
# returns immediately and the proxy is reparented to launchd (PID 1). It then
# survives /clear, CLI exit, and harness bg-tracking loss. Verify with:
#   ps -o pid,ppid,command -p "$(lsof -nP -tiTCP:$PORT -sTCP:LISTEN)"
# PPID should be 1.
#
# Usage:
#   ./start_proxy.sh                 # THE proxy: port 7800 -> logs_main, all levers on
#   PORT=7802 LOG_DIR=logs_scratch STRIP_COMPACT_CACHE=0 ./start_proxy.sh   # experiment arm
#
# Bare invocation = the one canonical proxy. Every feature is an env var
# (see the flag table in CLAUDE.md); any var you set overrides the default
# below, including set-to-empty/0. Code defaults already cover RELOCATE_*,
# SORT_TOOLS, CANARY, STRIP_SYSTEM_SECTIONS, WARMTH_LEDGER, WARMTH_PINGER,
# WARMTH_HOLD (the /warm-cache hold driver; spends nothing until armed).
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-7800}"
LOG_DIR="${LOG_DIR:-logs_main}"
OUT="${OUT:-proxy_${PORT}.out}"

# Canonical defaults for flags that are off in code ("-" not ":-" so an
# explicit empty/0 from the caller is respected):
export STRIP_COMPACT_CACHE="${STRIP_COMPACT_CACHE-1}"
export WARMTH_BLOCK_COLD_PING="${WARMTH_BLOCK_COLD_PING-1}"
export WARMTH_LOG_FILE="${WARMTH_LOG_FILE-1}"

# Refuse to double-bind the port.
if lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ERROR: port $PORT already has a listener (pid $(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN)). Kill it first." >&2
  exit 1
fi

# Subshell + nohup + disown => detached, reparented to PID 1.
(
  LOG_DIR="$LOG_DIR" PORT="$PORT" ${INJECT:+INJECT="$INJECT"} \
    nohup python3 -m uvicorn logproxy:app \
      --host 127.0.0.1 --port "$PORT" --log-level warning \
      >"$OUT" 2>&1 </dev/null &
  disown
)

# Give uvicorn a moment to bind, then report.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  pid="$(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  [ -n "$pid" ] && break
  sleep 0.3
done

if [ -n "${pid:-}" ]; then
  echo "started :$PORT pid=$pid  LOG_DIR=$LOG_DIR  out=$OUT"
  echo "  INJECT=${INJECT:-<off>}  INJECT_MARKER=${INJECT_MARKER:-<off>}  INJECT_TEXT=${INJECT_TEXT:-<default>}"
  ps -o pid,ppid,command -p "$pid"
else
  echo "FAILED to bind :$PORT — see $OUT" >&2
  tail -n 20 "$OUT" >&2 || true
  exit 1
fi
