#!/usr/bin/env bash
# (Re)start the OFFICIAL proxy on :7800 from the released code in
# releases/current — NOT from this working tree. State and data stay at the
# canonical lab paths regardless of which release runs:
#   LOG_DIR   = <lab>/logs_main      (captures + owner-scoped persisted state)
#   WARMTH_DB = <lab>/warmth.sqlite  (warmth ledger, holds, session identity)
#   OUT       = <lab>/proxy_7800.out
# so cutting a new release and re-running this swaps CODE only; sessions,
# holds and totals carry across (restart-amnesia). Only in-memory credentials
# drop — the auth bootstrap / next live turn re-donates them.
#
# Extra env for the official instance (e.g. SUBSCRIBERS_TOKEN) goes in
# release.env (gitignored, sourced here).
set -euo pipefail
cd "$(dirname "$0")"
LAB="$(pwd)"

if [ ! -e releases/current ]; then
  echo "ERROR: no releases/current — cut one first: ./release.sh vX.Y.Z" >&2
  exit 1
fi

if [ -f release.env ]; then
  set -a
  # shellcheck disable=SC1091
  . release.env
  set +a
fi

echo "official proxy <- releases/$(readlink releases/current)"
PORT=7800 \
LOG_DIR="$LAB/logs_main" \
WARMTH_DB="$LAB/warmth.sqlite" \
OUT="$LAB/proxy_7800.out" \
exec releases/current/restart_proxy.sh
