#!/usr/bin/env bash
# One-shot, no-fuss install for wirescope.
#
# Creates a self-contained virtual environment in ./.venv and installs the
# three pure-Python dependencies into it. A venv is the clean way: it sidesteps
# the "externally-managed-environment" error (PEP 668) you hit when pip refuses
# to touch a system/Homebrew Python, and it never pollutes anything global.
#
# Usage:
#   ./setup.sh            # create ./.venv and install deps
#   PYTHON=python3.12 ./setup.sh    # pick a specific interpreter
#   VENV=/path/to/venv  ./setup.sh  # put the venv elsewhere
#
# After this, just run ./start_proxy.sh — it auto-detects ./.venv.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
VENV="${VENV:-.venv}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "ERROR: '$PYTHON' not found. Install Python 3.9+ (https://www.python.org/downloads/)" >&2
  echo "  or point this at one:  PYTHON=python3.12 ./setup.sh" >&2
  exit 1
fi

# Refuse ancient Pythons early with a clear message rather than a cryptic
# install failure later.
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)'; then
  echo "ERROR: $("$PYTHON" -V) is too old — wirescope needs Python 3.9+." >&2
  exit 1
fi

# Create the venv if it isn't there yet (idempotent — re-running just re-installs).
if [ ! -x "$VENV/bin/python" ]; then
  echo "creating virtual environment in $VENV ..."
  if ! "$PYTHON" -m venv "$VENV" 2>/tmp/wirescope-venv-err; then
    cat /tmp/wirescope-venv-err >&2 || true
    echo >&2
    echo "ERROR: could not create the venv. On Debian/Ubuntu this usually means" >&2
    echo "  the venv module is missing — install it with:" >&2
    echo "    sudo apt install python3-venv" >&2
    echo "  then re-run ./setup.sh" >&2
    exit 1
  fi
fi

VPY="$VENV/bin/python"
echo "installing dependencies into $VENV ..."
"$VPY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true   # best-effort
"$VPY" -m pip install --quiet -r requirements.txt

echo
echo "✓ wirescope is ready."
echo "  start it:   ./start_proxy.sh          (port 7800 -> ./logs_main)"
echo "  point a CLI: ANTHROPIC_BASE_URL=http://localhost:7800 claude"
echo "  dashboard:  http://localhost:7800/_admin"
