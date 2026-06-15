#!/usr/bin/env bash
# Worktree cache-sharing probe (finding 3). Builds two git worktrees of the
# SAME project, runs a first-turn `claude -p` in each through both arms, then
# prices whether the 2nd worktree SHARES the system/CLAUDE.md segment (wirescope)
# or cold-writes its own copy (stock). See ../README.md for the WHY.
#
# SAFE: never touches :7800. Uses the same scratch arms as subagent-ab
# (7802 treatment / 7803 control). Prints start commands and exits if they're
# not up in the right shape — it does NOT auto-start them.
#
# Tunables (env): MODEL (default sonnet), A_PORT, B_PORT, *_DIR (must match the
# LOG_DIRs the running proxies were started with).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
FIXTURE="$HERE/../subagent-ab/fixtures/orderflow"   # reuse the realistic project

MODEL="${MODEL:-claude-sonnet-4-6}"
REPS="${REPS:-5}"
A_PORT="${A_PORT:-7802}"; B_PORT="${B_PORT:-7803}"
TREAT_URL="http://127.0.0.1:$A_PORT"; CTRL_URL="http://127.0.0.1:$B_PORT"
TREAT_DIR="${TREAT_DIR:-$REPO/logs_ab_treat}"
CTRL_DIR="${CTRL_DIR:-$REPO/logs_ab_ctrl}"
TOOLS="Read Grep Glob"
PROMPT="Reply with exactly the word: ok — and nothing else. Do not use any tools."

_pt() { curl -s "$1/_identity" 2>/dev/null \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['capabilities'].get('passthrough'))" 2>/dev/null \
  || echo down; }

if [ "$(_pt "$TREAT_URL")" != "False" ] || [ "$(_pt "$CTRL_URL")" != "True" ]; then
  cat >&2 <<EOF
Scratch arms not up in the expected shape.
Start them from the dev tree (NEVER :7800):
  PORT=$A_PORT LOG_DIR=logs_ab_treat WS_OMIT_DEFAULT=claudemd,useremail ./start_proxy.sh
  PORT=$B_PORT LOG_DIR=logs_ab_ctrl  WIRESCOPE_PASSTHROUGH=1            ./start_proxy.sh
EOF
  exit 1
fi

echo ">> arms: treatment=$TREAT_URL control=$CTRL_URL  model=$MODEL  reps=$REPS"

# run one first-turn session in $cwd through $url; echo its session_id
_run() {  # $1=url  $2=cwd
  ( cd "$2"
    ANTHROPIC_BASE_URL="$1" claude -p "$PROMPT" --output-format json \
        --model "$MODEL" --tools "$TOOLS" 2>/dev/null ) \
    | python3 -c "import sys,json;print(json.load(sys.stdin).get('session_id',''))"
}

T_A=""; T_B=""; C_A=""; C_B=""        # comma-accumulated, rep-aligned for probe.py
for rep in $(seq 1 "$REPS"); do
  # fresh worktrees each rep (fresh cwds): same project, different cwd + branch.
  BASE="$(mktemp -d)"; PROJ="$BASE/proj"; WTB="$BASE/wt-b"
  mkdir -p "$PROJ"; cp -R "$FIXTURE"/. "$PROJ"/
  ( cd "$PROJ"
    git init -q && git add -A && git -c user.email=ab@x -c user.name=ab commit -qm init
    git worktree add -q "$WTB" -b "feature-$rep" >/dev/null )

  echo ">> rep $rep/$REPS  (A=$PROJ  B=$WTB)"
  ta="$(_run "$TREAT_URL" "$PROJ")"; tb="$(_run "$TREAT_URL" "$WTB")"
  ca="$(_run "$CTRL_URL"  "$PROJ")"; cb="$(_run "$CTRL_URL"  "$WTB")"
  if [ -z "$ta" ] || [ -z "$tb" ] || [ -z "$ca" ] || [ -z "$cb" ]; then
    echo "ERROR rep $rep: a run returned no session_id (claude failed?)." >&2
    rm -rf "$BASE"; exit 1
  fi
  echo "   treat A=$ta B=$tb | ctrl A=$ca B=$cb"
  T_A="$T_A${T_A:+,}$ta"; T_B="$T_B${T_B:+,}$tb"
  C_A="$C_A${C_A:+,}$ca"; C_B="$C_B${C_B:+,}$cb"
  rm -rf "$BASE"                       # captures live in LOG_DIR by session_id; worktree not needed
done

echo
python3 "$HERE/probe.py" \
  --treat-dir "$TREAT_DIR" --ctrl-dir "$CTRL_DIR" \
  --treat-a "$T_A" --treat-b "$T_B" --ctrl-a "$C_A" --ctrl-b "$C_B"
