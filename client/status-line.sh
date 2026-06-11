#!/bin/bash
# Claude Code statusline: warns when a session is getting heavy, using several
# signals parsed from the transcript. Heaviness is judged on absolute magnitude
# (not % of window) since big/long context degrades quality regardless of cap.
# 
# Signals:
#   tokens   - current context tokens (from Claude Code's context_window)
#   turns    - real typed prompts. Preferred source: the PROXY's wire-derived
#              numbers (/_status .context: turns_in_context recomputed from
#              each request's model-visible history + turns_completed receipt-
#              counted off terminal responses); JSONL scan is the fallback.
#   since-↺  - turns since last compaction. Proxy source: turns_in_context
#              resets at /compact naturally (the summary replaces history).
#              Fallback: transcript scan for compact_boundary/isCompactSummary.
#   big      - largest single tool_result in context (~tokens), catches one
#              giant paste/output bloating the window
#   cache    - prompt-cache warmth POLLED FROM THE LOGPROXY
#              (`GET /_status?session=<sid>`), not inferred from the transcript.
#              The ledger is receipt-stamped off real API responses (stamped
#              only when usage proves caching happened), so it reflects the
#              actual server-side prefix state — including TTL slides this
#              session's JSONL never sees (keep-warm pings, forks). When a
#              /warm-cache hold is armed, the horizon extends past the bare
#              TTL: "+Np→HH:MM" = N auto-pings still to come, each buying
#              (ttl − margin) ≈ 55 min. Assumes the session runs THROUGH the
#              proxy (ANTHROPIC_BASE_URL); otherwise the field renders "∅".
#   cost     - TWO independent pricings of the same usage stream, side by
#              side: the CLI's own running estimate (stdin .cost.total_cost_usd,
#              priced client-side) vs the proxy's per-response pricing summed
#              in LOG_DIR/<sid>/_session.json (est_usd; "+?" appended when
#              unpriced_requests>0, i.e. the proxy total is a floor). Both
#              descend from the API's usage objects — divergence = different
#              price tables or different traffic attributed to the session.
#
# Transcript scan (heaviness signals only) is one streaming jq pass, cached on
# mtime so a long JSONL isn't re-read on every ~300ms render.
# Requires: jq, curl
#
# Tunable thresholds (override via env in settings.json):
WARN_TOKENS=${CC_WARN_TOKENS:-200000}     # yellow
HEAVY_TOKENS=${CC_HEAVY_TOKENS:-300000}   # red
WARN_TURNS=${CC_WARN_TURNS:-40}           # yellow (uses turns-since-compact)
HEAVY_TURNS=${CC_HEAVY_TURNS:-70}         # red
BIG_TOOL_TOKENS=${CC_BIG_TOOL_TOKENS:-25000}  # surface a single huge tool_result
CACHE_WARN=${CC_CACHE_WARN:-300}          # yellow when <= this many seconds left
PROXY_URL=${CC_PROXY_URL:-http://localhost:7800}  # logproxy base URL
PROXY_TIMEOUT=${CC_PROXY_TIMEOUT:-0.2}    # secs; a dead proxy must never stall the render
PROXY_LOG_DIR=${CC_PROXY_LOG_DIR:-$HOME/projects/proxy-lab/logs_main}  # proxy capture dir (per-session _session.json)

ESC=$(printf '\033')                      # real ESC byte, for color in %s args
input=$(cat)
# All scalar fields in ONE jq pass (@sh-quoted -> eval-safe).
eval "$(printf '%s' "$input" | jq -r '
  @sh "MODEL=\(.model.display_name // "?")",
  @sh "CWD=\(.workspace.current_dir // .cwd // ".")",
  @sh "SID=\(.session_id // "x")",
  @sh "TX=\(.transcript_path // "")",
  @sh "COST=\(.cost.total_cost_usd // "na")",
  @sh "TOK=\(
    if   .context_window.total_input_tokens != null then (.context_window.total_input_tokens|floor)
    elif .context_window.current_usage != null then
         ((.context_window.current_usage.input_tokens // 0)
        + (.context_window.current_usage.cache_creation_input_tokens // 0)
        + (.context_window.current_usage.cache_read_input_tokens // 0) | floor)
    else "na" end)"
')"
DIR=$(basename "$CWD")

# --- one /_status poll: warmth + hold + context heaviness ---------------------
# The proxy re-derives turns-in-context / biggest tool_result from each
# request's model-visible history (the wire IS the transcript: statelessly
# recomputed per turn, so resume/compact/restart-proof) and receipt-counts
# completed turns off terminal responses. When present these supersede the
# JSONL heuristics below (kept as fallback for unproxied sessions/proxy down).
st=$(curl -sf --max-time "$PROXY_TIMEOUT" \
     "${PROXY_URL}/_status?session=${SID}" 2>/dev/null)
read -r WSTATE WLEFT WTTL HMARG HPINGS HEXPTD PXSINCE PXBIG PXDONE <<< "$(printf '%s' "$st" | jq -r '
  ((.sessions // [])[0] // {}) as $s
  | "\($s.warmth.state // "absent") \($s.warmth.remaining_s // 0 | floor) \($s.warmth.ttl_s // 3600 | floor) \(.proxy.hold_config.margin_s // 300 | floor) \(if $s.hold then ($s.hold.pings // 0) else -1 end) \(if $s.hold then ($s.hold.expected_pings // 0) else -1 end) \($s.context.turns_in_context // -1) \($s.context.max_tool_result_chars // -1) \($s.turns_completed // -1)"' \
  2>/dev/null)"

# --- transcript stats: "<turns> <since_compact> <big_tool_chars>" ------------
# Source priority: (1) the proxy's wire-derived numbers above; (2) per-session
# state files published by cache-state-hook.sh at the end of each turn;
# (3) inline mtime-cached jq scan (cold start / no hook / no proxy).
TURNS=""; SINCE=""; BIGC=0
have_state=0
if [ "${PXSINCE:--1}" -ge 0 ]; then
  SINCE=$PXSINCE
  TURNS=$PXDONE
  [ "${PXDONE:--1}" -lt "$PXSINCE" ] && TURNS=$PXSINCE   # counter gap: floor at in-context
  BIGC=${PXBIG:-0}
  have_state=1
fi
sstate="${TMPDIR:-/tmp}/cc-stats-${SID}"
if [ "$have_state" = 0 ] && [ -f "$sstate" ]; then
  read -r TURNS SINCE BIGC _CTX < "$sstate"
  have_state=1
fi

# Fallback: cached on mtime; recomputed only when the transcript changes.
if [ "$have_state" = 0 ] && [ -n "$TX" ] && [ -f "$TX" ]; then
  mtime=$(stat -f %m "$TX" 2>/dev/null || stat -c %Y "$TX" 2>/dev/null)
  cache="${TMPDIR:-/tmp}/cc-sl-${SID}.stats"
  line=""
  [ -f "$cache" ] && line=$(cat "$cache")
  if [ "${line%%|*}" != "$mtime" ]; then
    stats=$(jq -n -r '
      def realuser($l): ($l.type=="user") and ($l.isSidechain|not) and ($l.isMeta|not)
        and ($l.isCompactSummary != true)
        and (($l.message.content|type=="string")
             or (($l.message.content|type=="array")
                 and ([$l.message.content[]?|select(.type=="tool_result")]|length==0)))
        and (($l.message.content | if type=="string" then . else (.[0].text // "") end)
             | (startswith("<command-")|not) and (startswith("<local-command-")|not));
      def iscompact($l): ($l.isCompactSummary==true) or ($l.type=="summary")
        or (($l.type=="system") and (($l.subtype//"")|test("compact";"i")));
      def toolmax($l): if ($l.type=="user" and ($l.message.content|type=="array"))
        then ([ $l.message.content[]? | select(.type=="tool_result")
                | (.content | if type=="string" then length
                              elif type=="array" then ([.[]?|(.text//"")|length]|add//0)
                              else 0 end) ] | max // 0)
        else 0 end;
      reduce inputs as $l ({turns:0, since:0, big:0};
          (if iscompact($l) then .since = 0 else . end)
        | (if realuser($l) then .turns += 1 | .since += 1 else . end)
        | (toolmax($l) as $m | if $m > .big then .big = $m else . end))
      | "\(.turns) \(.since) \(.big)"' "$TX" 2>/dev/null)
    echo "${mtime}|${stats}" > "$cache"
    line="${mtime}|${stats}"
  fi
  read -r TURNS SINCE BIGC <<< "${line#*|}"
fi

# turns-since-compact is the drift-relevant number; falls back to total turns
ETURNS=${SINCE:-$TURNS}
BIGTOK=$(( ${BIGC:-0} / 4 ))   # ~tokens from chars

# --- severity: worst of token / turn dimensions ------------------------------
lvl=0
if   [ "$TOK" != "na" ] && [ "$TOK" -ge "$HEAVY_TOKENS" ]; then lvl=2
elif [ "$TOK" != "na" ] && [ "$TOK" -ge "$WARN_TOKENS" ];  then lvl=1; fi
if [ -n "$ETURNS" ]; then
  if   [ "$ETURNS" -ge "$HEAVY_TURNS" ] && [ "$lvl" -lt 2 ]; then lvl=2
  elif [ "$ETURNS" -ge "$WARN_TURNS" ]  && [ "$lvl" -lt 1 ]; then lvl=1; fi
fi
R="\033[0m"
case $lvl in
  2) C="\033[31m"; LABEL="  ⚠️ HEAVY — /compact or fresh session";;
  1) C="\033[33m"; LABEL="  ⚠ getting heavy";;
  *) C="\033[32m"; LABEL="";;
esac

# --- assemble suffix fields --------------------------------------------------
SUFFIX=""
if [ -n "$TURNS" ]; then
  if [ -n "$SINCE" ] && [ "$SINCE" -lt "$TURNS" ]; then SUFFIX=" | ${ESC}[36mturn: ${SINCE}↺${ESC}[0m"   # ↺ = since last compact
  else SUFFIX=" | ${ESC}[36mturn: ${TURNS}${ESC}[0m"; fi
fi
[ "$BIGTOK" -ge "$BIG_TOOL_TOKENS" ] && SUFFIX="${SUFFIX} | ${ESC}[33mbig: $(( BIGTOK/1000 ))k${ESC}[0m"

# --- prompt-cache warmth (+ armed hold): from the /_status poll above ---------
# Warm (remaining_s left), cold (busted — the cheapest moment to /compact), or
# absent (session never went through the proxy, or proxy down) rendered as a
# dim "∅". The CLI does NOT re-render the statusline while idle, so remaining_s
# is converted to the absolute expiry time — a frozen render stays truthful:
# glance at the clock to know if it's gone cold. A /warm-cache hold extends
# that horizon: each remaining auto-ping re-warms just inside the margin, so
#   effective expiry ≈ ttl expiry + (expected_pings − pings) × (ttl − margin)
# shown as "+Np→HH:MM". Near-TTL yellow is suppressed while pings remain (the
# ping, not the user, handles it). Hold only refreshes WARM prefixes — a cold
# cache renders ❄️ regardless of the hold.
if [ "$WSTATE" = "warm" ]; then
  EXP=$(( $(date +%s) + WLEFT ))
  HM=$(date -r "$EXP" +%H:%M 2>/dev/null || date -d "@$EXP" +%H:%M 2>/dev/null)
  NREM=0
  if [ "${HPINGS:--1}" -ge 0 ]; then
    NREM=$(( HEXPTD - HPINGS )); [ "$NREM" -lt 0 ] && NREM=0
  fi
  FIELD="🔥cache→${HM}"
  if [ "$NREM" -gt 0 ]; then
    HEXP=$(( EXP + NREM * (WTTL - HMARG) ))
    HHM=$(date -r "$HEXP" +%H:%M 2>/dev/null || date -d "@$HEXP" +%H:%M 2>/dev/null)
    FIELD="🔥cache→${HM} +${NREM}p→${HHM}"
  fi
  if [ "$WLEFT" -le "$CACHE_WARN" ] && [ "$NREM" -eq 0 ]; then
    SUFFIX="${SUFFIX} | ${ESC}[33m${FIELD}${ESC}[0m"
  else
    SUFFIX="${SUFFIX} | ${ESC}[38;5;208m${FIELD}${ESC}[0m"
  fi
elif [ "$WSTATE" = "cold" ]; then
  SUFFIX="${SUFFIX} | ${ESC}[31m❄️cache busted /compact${ESC}[0m"
else
  SUFFIX="${SUFFIX} | ${ESC}[2mcache ∅${ESC}[0m"
fi

# --- cost: the CLI's estimate vs the proxy's, same usage stream priced twice --
# CLI side arrives on stdin (priced client-side, resets with the CLI session);
# proxy side is the running est_usd it accumulates per wire-session from each
# captured response (includes side-calls like the title generator and refused
# turns' cache writes — traffic the CLI may price differently or not at all).
PUSD=""; PUNP=0
sfile="$PROXY_LOG_DIR/$SID/_session.json"
if [ -f "$sfile" ]; then
  read -r PUSD PUNP <<< "$(jq -r \
    '"\(.est_usd // "") \(.unpriced_requests // 0)"' "$sfile" 2>/dev/null)"
fi
CSHOW="–"; PSHOW="–"
[ "$COST" != "na" ] && CSHOW=$(printf '%.4f' "$COST" 2>/dev/null)
[ -n "$PUSD" ]      && PSHOW=$(printf '%.4f' "$PUSD" 2>/dev/null)
[ "${PUNP:-0}" -gt 0 ] 2>/dev/null && PSHOW="${PSHOW}+?"   # unpriced traffic: floor
if [ "$CSHOW" != "–" ] || [ "$PSHOW" != "–" ]; then
  SUFFIX="${SUFFIX} | ${ESC}[35m\$cli:${CSHOW} px:${PSHOW}${ESC}[0m"
fi

# --- token field -------------------------------------------------------------
if [ "$TOK" = "na" ]; then
  printf "%s | [%s] ${C}ctx: n/a${LABEL}${R}%s\n" "$DIR" "$MODEL" "$SUFFIX"
  exit 0
fi
printf "%s | [%s] ${C}ctx: %dk${LABEL}${R}%s\n" "$DIR" "$MODEL" "$(( TOK/1000 ))" "$SUFFIX"
