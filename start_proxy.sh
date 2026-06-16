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

# Resolve the Python interpreter: an active venv wins, then a local ./.venv
# (what setup.sh creates), else system python3. If the chosen interpreter can't
# import uvicorn, bootstrap a local venv automatically — so a fresh clone needs
# only `./start_proxy.sh`, no pip dance. (A user who already has the deps on
# their own interpreter is untouched: the import check passes, nothing is built.)
if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
  PY="$VIRTUAL_ENV/bin/python"
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi
if ! "$PY" -c 'import uvicorn' >/dev/null 2>&1; then
  if [ -x ./setup.sh ]; then
    echo "dependencies missing — bootstrapping a local venv via ./setup.sh ..."
    ./setup.sh
    PY=".venv/bin/python"
  else
    echo "ERROR: uvicorn isn't available to '$PY'. Run ./setup.sh (recommended) or" >&2
    echo "  pip install -r requirements.txt into your environment." >&2
    exit 1
  fi
fi

# Operator policy / secrets: load release.env (gitignored, per-machine — the
# SAME file run_release.sh sources, so start_proxy.sh and the release path get
# identical deployment-local flags like WS_SPAWNER_HINT / WS_OMIT_DEFAULT /
# SUBSCRIBERS_TOKEN). Caller-supplied env STILL WINS: we only fill a key that
# isn't already in the environment, so `FOO=0 ./start_proxy.sh` overrides the
# file just like it overrides the code defaults below. (A fresh clone has no
# release.env — recreate it per machine; that's how the off-by-code-default
# discoverability flags become the local default behavior on a new laptop.)
if [ -f release.env ]; then
  while IFS= read -r line; do
    case "$line" in ''|[[:space:]]*|\#*) continue ;; esac   # skip blanks + comments
    key="${line%%=*}"
    printenv "$key" >/dev/null 2>&1 || export "${line?}"
  done < release.env
fi

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
    nohup "$PY" -m uvicorn logproxy:app \
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
