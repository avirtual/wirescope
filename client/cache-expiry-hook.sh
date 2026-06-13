#!/bin/bash
# UserPromptSubmit hook — flags a cold prompt cache at the moment you submit.
#
# Rationale: an expired cache is the cheapest moment to /compact. Continuing
# instead silently re-caches the now-stale context at full write price, and the
# saving opportunity is gone. This nudges you to compact first.
#
# Warmth source: the wirescope warmth ledger (`GET /_warm?session=<sid>`), not
# transcript timestamps. The ledger is receipt-stamped off real API responses,
# so it sees TTL slides this session's JSONL never records (keep-warm pings,
# forks) — the old transcript-anchored estimate would false-positive on a
# pinged session. Requires the session to run through the proxy
# (ANTHROPIC_BASE_URL=$CC_PROXY_URL); unproxied sessions and a dead proxy are
# treated as "can't judge" and never nudged.
#
# Enable in settings.json:
#   "hooks": { "UserPromptSubmit": [ { "hooks": [
#     { "type": "command", "command": "~/.claude/cache-expiry-hook.sh" } ] } ] }
#
# Modes:
#   block (default)  exit 2 — blocks THIS submission and shows the note to you,
#                    so you can choose to /compact. To avoid a re-block loop
#                    (a busted cache stays busted until a turn runs, which a
#                    block prevents) it only fires ONCE per idle episode: the
#                    immediate resubmit goes through untouched.
#   advisory         CC_CACHE_HOOK_BLOCK=0 — never blocks; prints the note once
#                    per episode (visible in transcript/Ctrl-R), prompt passes.
#
# Floors: a /compact has a fixed cost (read the whole context + emit a summary),
# so it's only worth nudging once there's enough to claw back. Stay silent when
# the context is small or the session was just compacted / barely started:
#   CC_CACHE_MIN_TOKENS  context tokens below which a compact saves too little
#   CC_CACHE_MIN_TURNS   turns-since-compact below which there's nothing to fold
#
# Tunables: CC_PROXY_URL, CC_PROXY_TIMEOUT, CC_CACHE_HOOK_BLOCK (1/0),
#           CC_CACHE_MIN_TOKENS, CC_CACHE_MIN_TURNS.
# Requires: jq, curl
PROXY_URL=${CC_PROXY_URL:-http://localhost:7800}
PROXY_TIMEOUT=${CC_PROXY_TIMEOUT:-0.2}
BLOCK=${CC_CACHE_HOOK_BLOCK:-1}
MIN_TOKENS=${CC_CACHE_MIN_TOKENS:-20000}
MIN_TURNS=${CC_CACHE_MIN_TURNS:-4}

input=$(cat)
SID=$(printf '%s' "$input" | jq -r '.session_id // "x"')
TX=$(printf '%s' "$input" | jq -r '.transcript_path // ""')

# Warmth verdict from the proxy. Anything other than a definite found-but-
# lapsed row means stay silent: warm → nothing to save; not found / proxy
# down / parse error → can't judge, never block on a guess.
warm=$(curl -sf --max-time "$PROXY_TIMEOUT" \
       "${PROXY_URL}/_warm?session=${SID}" 2>/dev/null) || exit 0
read -r WFOUND WWARM WHASH WAGE WTTL <<< "$(printf '%s' "$warm" | jq -r \
  '"\(.found) \(.warm) \(.hash // "x") \(.age_s // 0 | floor) \(.ttl_s // 0)"' 2>/dev/null)"
[ "$WFOUND" = "true" ] && [ "$WWARM" = "false" ] || exit 0

# Too little to gain: small context, or a just-compacted / barely-started
# session. The summary call's fixed overhead wouldn't pay back here, so the
# nudge would be noise. since/ctx are heaviness signals only the transcript
# has (the proxy answers warmth, not content); defs mirror status-line.sh.
[ -z "$TX" ] || [ ! -f "$TX" ] && exit 0
read -r SINCE CTX <<< "$(jq -n -r '
  def iscompact($l): ($l.isCompactSummary==true) or ($l.type=="summary")
    or (($l.type=="system") and (($l.subtype//"")|test("compact";"i")));
  def realuser($l): ($l.type=="user") and (($l.isSidechain//false)|not) and (($l.isMeta//false)|not)
    and ($l.isCompactSummary != true)
    and (($l.message.content|type=="string")
         or (($l.message.content|type=="array")
             and ([$l.message.content[]?|select(.type=="tool_result")]|length==0)))
    and (($l.message.content | if type=="string" then . else (.[0].text // "") end)
         | (startswith("<command-")|not) and (startswith("<local-command-")|not));
  def ctxtok($l): if ($l.type=="assistant") and ($l.message.usage != null)
    then (($l.message.usage.input_tokens//0)
        + ($l.message.usage.cache_read_input_tokens//0)
        + ($l.message.usage.cache_creation_input_tokens//0))
    else 0 end;
  reduce inputs as $l ({since:0, ctx:0};
      (if iscompact($l) then .since = 0 else . end)
    | (if realuser($l) then .since += 1 else . end)
    | (ctxtok($l) as $c | if $c > 0 then .ctx = $c else . end))
  | "\(.since) \(.ctx)"' "$TX" 2>/dev/null)"
[ "${CTX:-0}"   -lt "$MIN_TOKENS" ] 2>/dev/null && exit 0
[ "${SINCE:-0}" -lt "$MIN_TURNS"  ] 2>/dev/null && exit 0

# Warn once per idle episode. The ledger's head hash only changes when a new
# turn lands, so keying the guard on it means: warn on the first submit after
# expiry, then let the resubmit (same hash) through — no deadlock.
GUARD="${TMPDIR:-/tmp}/cc-cache-warned-${SID}"
[ "$(cat "$GUARD" 2>/dev/null)" = "$WHASH" ] && exit 0
printf '%s' "$WHASH" > "$GUARD"

OVER=$(( WAGE - WTTL )); [ "$OVER" -lt 0 ] && OVER=0
MSG="⚠ prompt cache expired ~$(( OVER/60 ))m ago (TTL $(( WTTL/60 ))m). Cheapest moment to /compact — continuing now re-caches stale context at full price. (resubmit to proceed)"
printf '%s\n' "$MSG" >&2
[ "$BLOCK" = "1" ] && exit 2
exit 0
