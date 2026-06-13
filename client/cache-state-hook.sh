#!/bin/bash
# Stop hook — end-of-turn producer of per-session state for the statusline and
# the cache-expiry hook. Runs ONCE when the turn ends, instead of letting the
# statusline re-scan the whole transcript on every render (the JSONL grows
# mid-turn on each tool_result, so a render-time scan is O(n)-per-append).
#
# It writes one tiny session-keyed file in TMPDIR, a single line of
# space-separated values (no JSON parse at render time — the statusline just
# `read`s it):
#
#   cc-stats-<SID>   "<turns> <since> <big> <ctx>"    heaviness signals
#
# (The old cc-cache-<SID> TTL-anchor file is gone: cache warmth is now polled
# live from the wirescope warmth ledger by status-line.sh / cache-expiry-hook.sh,
# which sees TTL slides the transcript never records.)
#
# Writes are atomic (tmp in the same dir + rename) so the frequently-reading
# statusline never catches a torn file. Non-blocking: always exit 0.
#
# Enable in settings.json:
#   "hooks": { "Stop": [ { "hooks": [
#     { "type": "command", "command": "$CLAUDE_PROJECT_DIR/.claude/cache-state-hook.sh" } ] } ] }
# Requires: jq

input=$(cat)
SID=$(printf '%s' "$input" | jq -r '.session_id // "x"')
TX=$(printf '%s' "$input" | jq -r '.transcript_path // ""')
[ -z "$TX" ] || [ ! -f "$TX" ] && exit 0

# One streaming pass — same field defs as status-line.sh / cache-expiry-hook.sh
# so all three agree on what counts as a real turn, a compaction, etc.
read -r TURNS SINCE BIGC LASTTS DTTL CTX <<< "$(jq -n -r '
  def realuser($l): ($l.type=="user") and (($l.isSidechain//false)|not) and (($l.isMeta//false)|not)
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
  def lastts($l): if ($l.timestamp != null) and ($l.type=="user" or $l.type=="assistant")
    then (try ($l.timestamp | sub("\\.[0-9]+Z$";"Z") | fromdateiso8601) catch 0) else 0 end;
  def cttl($l): ($l.message.usage.cache_creation) as $c
    | if   $c == null                                then 0
      elif (($c.ephemeral_1h_input_tokens // 0) > 0) then 3600
      elif (($c.ephemeral_5m_input_tokens // 0) > 0) then 300
      else 0 end;
  def ctxtok($l): if ($l.type=="assistant") and ($l.message.usage != null)
    then (($l.message.usage.input_tokens//0)
        + ($l.message.usage.cache_read_input_tokens//0)
        + ($l.message.usage.cache_creation_input_tokens//0))
    else 0 end;
  reduce inputs as $l ({turns:0, since:0, big:0, last:0, ttl:0, ctx:0};
      (if iscompact($l) then .since = 0 else . end)
    | (if realuser($l) then .turns += 1 | .since += 1 else . end)
    | (toolmax($l) as $m | if $m > .big then .big = $m else . end)
    | (lastts($l) as $t | if $t > .last then .last = $t else . end)
    | (cttl($l)   as $u | if $u > 0 then .ttl = $u else . end)
    | (ctxtok($l) as $c | if $c > 0 then .ctx = $c else . end))
  | "\(.turns) \(.since) \(.big) \(.last) \(.ttl) \(.ctx)"' "$TX" 2>/dev/null)"

[ "${LASTTS:-0}" -gt 0 ] 2>/dev/null || exit 0   # nothing meaningful yet

D="${TMPDIR:-/tmp}"
# atomic publish: write a sibling temp file, then rename over the target.
t=$(mktemp "$D/cc-state.XXXXXX") || exit 0
printf '%s %s %s %s\n' "${TURNS:-0}" "${SINCE:-0}" "${BIGC:-0}" "${CTX:-0}" > "$t" && mv -f "$t" "$D/cc-stats-${SID}"
exit 0
