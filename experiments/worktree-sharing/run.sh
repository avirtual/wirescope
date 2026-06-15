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

# --- build two worktrees of one project (same files, different cwd + branch) ---
BASE="$(mktemp -d)"
PROJ="$BASE/proj"; WTB="$BASE/wt-b"
mkdir -p "$PROJ"
cp -R "$FIXTURE"/. "$PROJ"/
( cd "$PROJ"
  git init -q && git add -A && git -c user.email=ab@x -c user.name=ab commit -qm init
  git worktree add -q "$WTB" -b feature-b >/dev/null )
echo ">> worktree A = $PROJ   (branch main)"
echo ">> worktree B = $WTB    (branch feature-b)"
echo ">> arms: treatment=$TREAT_URL control=$CTRL_URL  model=$MODEL"

# run one first-turn session in $cwd through $url; echo its session_id
_run() {  # $1=url  $2=cwd
  ( cd "$2"
    ANTHROPIC_BASE_URL="$1" claude -p "$PROMPT" --output-format json \
        --model "$MODEL" --tools "$TOOLS" 2>/dev/null ) \
    | python3 -c "import sys,json;print(json.load(sys.stdin).get('session_id',''))"
}

echo; echo ">> TREATMENT  A (warm) then B (share?) ..."
T_A="$(_run "$TREAT_URL" "$PROJ")"; echo "   treat-A session=$T_A"
T_B="$(_run "$TREAT_URL" "$WTB")";  echo "   treat-B session=$T_B"
echo ">> CONTROL    A (warm) then B (share?) ..."
C_A="$(_run "$CTRL_URL" "$PROJ")";  echo "   ctrl-A  session=$C_A"
C_B="$(_run "$CTRL_URL" "$WTB")";   echo "   ctrl-B  session=$C_B"

if [ -z "$T_A$T_B$C_A$C_B" ] || [ -z "$T_B" ] || [ -z "$C_B" ]; then
  echo "ERROR: a run did not return a session_id (claude failed?)." >&2
  echo "worktrees left at $BASE for inspection." >&2
  exit 1
fi

echo
python3 "$HERE/probe.py" \
  --treat-dir "$TREAT_DIR" --ctrl-dir "$CTRL_DIR" \
  --treat-a "$T_A" --treat-b "$T_B" --ctrl-a "$C_A" --ctrl-b "$C_B"

echo
echo ">> worktrees left at $BASE (temp dir; safe to rm -rf when done)."
