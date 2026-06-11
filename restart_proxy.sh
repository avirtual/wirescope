#!/usr/bin/env bash
# Restart the proxy on $PORT: kill the current listener (if any), wait for the
# port to free, then hand off to start_proxy.sh. All env vars pass through, so
# any start_proxy.sh invocation works here too:
#   ./restart_proxy.sh                          # restart THE proxy on :7800
#   PORT=7802 LOG_DIR=logs_scratch ./restart_proxy.sh   # restart an experiment arm
#
# Safe-ish since the restart-amnesia fix (holds/totals/identity persist in
# warmth.sqlite); the one loss is in-memory credentials — restored sessions sit
# awaiting_auth until the account's next live turn (or the auth bootstrap)
# re-donates them. Avoid restarting mid-experiment.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-7800}"

pid="$(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$pid" ]; then
  echo "stopping :$PORT pid=$pid"
  kill $pid
  # Wait for the listener to release the port (TERM is usually instant).
  for _ in $(seq 1 20); do
    lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
    sleep 0.3
  done
  if lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "ERROR: :$PORT still bound after kill (pid $(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN)) — not starting." >&2
    exit 1
  fi
else
  echo ":$PORT had no listener — starting fresh"
fi

exec ./start_proxy.sh
