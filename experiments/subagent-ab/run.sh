#!/usr/bin/env bash
# Reproduce the subagent fan-out A/B (treatment vs byte-verbatim passthrough).
# See ../README.md for the methodology and WHY each knob is set the way it is.
#
# This script is SAFE: it never touches :7800 (the live proxy). It uses two
# scratch ports (7802 treatment / 7803 control). If they aren't already up in
# the right shape it prints the exact start commands and exits — it does NOT
# auto-start them, so it can't clobber a port the operator is using.
#
# Tunables (env): MODEL (default sonnet), REPS (default 6), A_PORT, B_PORT.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PROJECT="$HERE/fixtures/orderflow"     # the cwd the agent runs in (CLAUDE.md auto-loads from here)

MODEL="${MODEL:-claude-sonnet-4-6}"
REPS="${REPS:-6}"
A_PORT="${A_PORT:-7802}"               # TREATMENT (transforms on)
B_PORT="${B_PORT:-7803}"               # CONTROL   (WIRESCOPE_PASSTHROUGH=1)
A_URL="http://127.0.0.1:$A_PORT"
B_URL="http://127.0.0.1:$B_PORT"
A_DIR="$REPO/logs_ab_treat"
B_DIR="$REPO/logs_ab_ctrl"
MANIFEST="$REPO/ab_run_subagent.json"

# A dev who already trimmed their roster sensibly (Workflow etc. gone). We do
# NOT hand the control a naive full-33-tool roster — that would cook the
# baseline. Both arms get the SAME realistic toolset; we measure only the
# INCREMENTAL gain wirescope's per-subagent shaping adds on top.
TOOLS="Read Edit Write Bash Glob Grep Task"

_pt() {  # echo the passthrough capability of a /_identity, or "down"
  curl -s "$1/_identity" 2>/dev/null \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['capabilities'].get('passthrough'))" 2>/dev/null \
    || echo down
}

a_state="$(_pt "$A_URL")"; b_state="$(_pt "$B_URL")"
if [ "$a_state" != "False" ] || [ "$b_state" != "True" ]; then
  cat >&2 <<EOF
Scratch arms not up in the expected shape (A=$A_PORT treatment, B=$B_PORT control).
  arm A ($A_URL) passthrough=$a_state   (want False — transforms ON)
  arm B ($B_URL) passthrough=$b_state   (want True  — verbatim control)

Start them from the dev tree (detached; start_proxy.sh refuses a bound port):

  PORT=$A_PORT LOG_DIR=logs_ab_treat WS_OMIT_DEFAULT=claudemd,useremail ./start_proxy.sh
  PORT=$B_PORT LOG_DIR=logs_ab_ctrl  WIRESCOPE_PASSTHROUGH=1            ./start_proxy.sh

Then re-run this script. (Never use :7800 — that's the live proxy.)
EOF
  exit 1
fi

echo ">> treatment=$A_URL  control=$B_URL  model=$MODEL  reps=$REPS"
echo ">> cwd for claude = $PROJECT"

cd "$PROJECT"
python3 "$REPO/ab_run.py" \
  "@$HERE/prompts/treatment.txt" \
  --b-prompt "@$HERE/prompts/control.txt" \
  --a-url "$A_URL" --a-dir "$A_DIR" \
  --b-url "$B_URL" --b-dir "$B_DIR" \
  -n "$REPS" -o "$MANIFEST" \
  --claude-arg=--model --claude-arg="$MODEL" \
  --claude-arg=--tools --claude-arg="$TOOLS"

echo
echo "========================= FULL ($REPS reps) ========================="
python3 "$REPO/ab_analyze.py" --manifest "$MANIFEST"

if [ "$REPS" -gt 1 ]; then
  echo
  echo "=========== WARM STEADY-STATE (drop the cold rep 1) ==========="
  echo "(rep 1 cold-writes the treatment's novel prefix while the control reads"
  echo " the account's pre-warmed vanilla prefix — steady state is the honest \$.)"
  python3 "$REPO/ab_analyze.py" --manifest "$MANIFEST" --last "$((REPS-1))"
fi
