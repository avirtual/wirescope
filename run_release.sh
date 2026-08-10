#!/usr/bin/env bash
# (Re)start a proxy from the released code in releases/current — NOT from this
# working tree. State and data stay at the canonical lab paths regardless of
# which release runs:
#   LOG_DIR   = <lab>/logs_main      (captures + owner-scoped persisted state)
#   WARMTH_DB = <lab>/warmth.sqlite  (warmth ledger, holds, session identity)
#   OUT       = <lab>/proxy_<port>.out
# so cutting a new release and re-running this swaps CODE only; sessions,
# holds and totals carry across (restart-amnesia). Only in-memory credentials
# drop — the auth bootstrap / next live turn re-donates them.
#
# Extra env for the official instance (e.g. SUBSCRIBERS_TOKEN) goes in
# release.env (gitignored, sourced here).
#
# ── OWNERSHIP GUARD ─────────────────────────────────────────────────────────
# Since 2026-07-03 :7800 is normally CLODEX-MANAGED-VENDORED (it runs from
# wb-wrap-ui/vendor/wirescope, not from this lab). This script's next act is
# restart_proxy.sh, whose first act is `kill` on whatever LISTENS on $PORT —
# so run bare it would take down the managed proxy and every warm prefix with
# it. The script cannot see whose proxy that is; it can only see where the
# listener was launched from. So: REFUSE unless the listener is ours.
#   ours     = cwd is inside this lab (a hand-run release / dev instance)
#   foreign  = anything else, incl. a cwd we cannot read → refuse (fail closed)
#   absent   = no listener at all → nothing to kill, proceed
# Override with RUN_RELEASE_FORCE=1 once you have decided the kill is right.
# RUN_RELEASE_CHECK_ONLY=1 runs the guard and exits without starting anything.
set -euo pipefail
cd "$(dirname "$0")"
LAB="$(pwd)"

PORT="${PORT:-7800}"

# --- ownership guard -------------------------------------------------------
foreign=""
for pid in $(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true); do
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
  case "${cwd:-}" in
    "$LAB"|"$LAB"/*) ;;                       # ours — a hand-run from this lab
    *) foreign="$foreign $pid(${cwd:-cwd-unreadable})" ;;
  esac
done

if [ -n "$foreign" ]; then
  if [ "${RUN_RELEASE_FORCE:-0}" = "1" ]; then
    echo "WARNING: :$PORT is held by a FOREIGN listener —$foreign" >&2
    echo "WARNING: RUN_RELEASE_FORCE=1 — killing it anyway." >&2
  else
    cat >&2 <<EOF
REFUSING to restart :$PORT — its listener was not started from this lab.
  listener:$foreign
  this lab: $LAB

That is almost certainly the clodex-managed vendored proxy. Restarting it from
here reverts the deployment model (the 2026-07-15 rogue-instance bug) and busts
every warm cache prefix in flight.

To DEPLOY a release under the managed model, you do not run this script at all:
  ./release.sh vX.Y.Z          # tag + push to origin avirtual/wirescope
  then tell clodex which tag to vendor (it re-runs its own vendor script).

To run a hand-managed instance for dev/scratch, pick a free port:
  PORT=7801 LOG_DIR=\$PWD/logs_scratch ./run_release.sh

If you really mean to kill the listener above: RUN_RELEASE_FORCE=1 ./run_release.sh
EOF
    exit 1
  fi
fi

[ "${RUN_RELEASE_CHECK_ONLY:-0}" = "1" ] && { echo "guard ok for :$PORT"; exit 0; }

# --- normal path -----------------------------------------------------------
if [ ! -e releases/current ]; then
  echo "ERROR: no releases/current — cut one first: ./release.sh vX.Y.Z" >&2
  exit 1
fi

# LOG_DIR defaults to the lab archive, which is right for a hand-run :7800 and
# WRONG for a scratch arm (it would write into the frozen :7800 corpus — the
# same hazard OPERATIONS.md flags for restart_proxy.sh). So off the default
# port, make the caller say it.
if [ "$PORT" != "7800" ] && [ -z "${LOG_DIR:-}" ]; then
  echo "ERROR: PORT=$PORT needs an explicit LOG_DIR (default logs_main is the :7800 corpus)." >&2
  echo "   e.g. PORT=$PORT LOG_DIR=\$PWD/logs_scratch ./run_release.sh" >&2
  exit 1
fi

if [ -f release.env ]; then
  set -a
  # shellcheck disable=SC1091
  . release.env
  set +a
fi

echo "proxy :$PORT <- releases/$(readlink releases/current)"
PORT="$PORT" \
LOG_DIR="${LOG_DIR:-$LAB/logs_main}" \
WARMTH_DB="${WARMTH_DB:-$LAB/warmth.sqlite}" \
OUT="${OUT:-$LAB/proxy_$PORT.out}" \
exec releases/current/restart_proxy.sh
