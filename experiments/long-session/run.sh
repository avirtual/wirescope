#!/usr/bin/env bash
# Long single-cwd session through both arms, to measure MAIN-LINE transform
# amortization (no subagents, no worktrees). See ../README.md for the question.
#
# SAFE: never touches :7800. Same scratch arms as the other experiments
# (7802 treatment / 7803 control). Prints start commands and exits if they're
# not up in the right shape.
#
# Tunables (env): MODEL (default sonnet), A_PORT, B_PORT, *_DIR.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
FIXTURE="$HERE/../subagent-ab/fixtures/orderflow"

MODEL="${MODEL:-claude-sonnet-4-6}"
A_PORT="${A_PORT:-7802}"; B_PORT="${B_PORT:-7803}"
TREAT_URL="http://127.0.0.1:$A_PORT"; CTRL_URL="http://127.0.0.1:$B_PORT"
TREAT_DIR="${TREAT_DIR:-$REPO/logs_ab_treat}"
CTRL_DIR="${CTRL_DIR:-$REPO/logs_ab_ctrl}"
TOOLS="Read Grep Glob"

_pt() { curl -s "$1/_identity" 2>/dev/null \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['capabilities'].get('passthrough'))" 2>/dev/null \
  || echo down; }
if [ "$(_pt "$TREAT_URL")" != "False" ] || [ "$(_pt "$CTRL_URL")" != "True" ]; then
  cat >&2 <<EOF
Scratch arms not up in the expected shape. Start from the dev tree (NEVER :7800):
  PORT=$A_PORT LOG_DIR=logs_ab_treat WS_OMIT_DEFAULT=claudemd,useremail ./start_proxy.sh
  PORT=$B_PORT LOG_DIR=logs_ab_ctrl  WIRESCOPE_PASSTHROUGH=1            ./start_proxy.sh
EOF
  exit 1
fi

mapfile -t PROMPTS < "$HERE/prompts.txt"
echo ">> ${#PROMPTS[@]} turns/arm  model=$MODEL  treat=$TREAT_URL ctrl=$CTRL_URL"

# one fresh project copy, single stable cwd (a real small git repo)
BASE="$(mktemp -d)"; PROJ="$BASE/proj"; mkdir -p "$PROJ"
cp -R "$FIXTURE"/. "$PROJ"/
( cd "$PROJ" && git init -q && git add -A && git -c user.email=ab@x -c user.name=ab commit -qm init )

# drive the whole prompt sequence as ONE resumed session through $url; echo sid
_drive() {  # $1=url  $2=label
  local url="$1" label="$2" sid="" out
  cd "$PROJ"
  for i in "${!PROMPTS[@]}"; do
    if [ -z "$sid" ]; then
      out=$(ANTHROPIC_BASE_URL="$url" claude -p "${PROMPTS[$i]}" --output-format json \
            --model "$MODEL" --tools "$TOOLS" 2>/dev/null)
      sid=$(printf '%s' "$out" | python3 -c "import sys,json;print(json.load(sys.stdin).get('session_id',''))")
    else
      ANTHROPIC_BASE_URL="$url" claude -p --resume "$sid" "${PROMPTS[$i]}" --output-format json \
            --model "$MODEL" --tools "$TOOLS" >/dev/null 2>&1
    fi
    [ -z "$sid" ] && { echo "ERROR $label turn $((i+1)): no session_id" >&2; return 1; }
    printf '   %s turn %2d/%d done\n' "$label" "$((i+1))" "${#PROMPTS[@]}" >&2
  done
  printf '%s' "$sid"
}

echo ">> TREATMENT session ..."; T_SID="$(_drive "$TREAT_URL" treat)"
echo "   treat session=$T_SID"
echo ">> CONTROL session ..."  ; C_SID="$(_drive "$CTRL_URL" ctrl)"
echo "   ctrl  session=$C_SID"
rm -rf "$BASE"

echo
python3 "$HERE/trajectory.py" \
  --treat-dir "$TREAT_DIR" --ctrl-dir "$CTRL_DIR" \
  --treat-sid "$T_SID" --ctrl-sid "$C_SID"
echo
echo ">> full-corpus pricing of the two sessions:"
python3 "$REPO/ab_analyze.py" "$TREAT_DIR" "$CTRL_DIR" \
  --sessions-a "$T_SID" --sessions-b "$C_SID" 2>/dev/null | sed -n '/HEADLINE/p'
