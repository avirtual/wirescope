# Compaction on the wire — measured (2026-09-04)

Corpus: 2026-06-10 → 2026-09-04, 875 compaction events, 142 sessions.
Sources: live clodex capture dir (223k requests) + logs_main (54k requests).

## Detector
Compact instruction is appended as a trailing text block on the FINAL USER
message, `max_tokens` 64000 (32000 on sonnet). Detector walks BACK over
trailing non-user messages (opus-4.8 rides a `role:"system"` roster behind it)
and requires the anchor in that block only.
- raw grep 2,108 -> 875 real. 1,008 were agents READING proxylab's own
  `_COMPACT_ANCHORS` source; 142 were `/_ping` replays (`max_tokens:1`);
  8 were my own A/B replay arms; 12 had `output<200` (aborted).

## Sizes
              n     ctx p50     ctx p90    out p50   out p90   ratio p50
  ALL       875     159,089     205,502     10,807    13,675     0.068
  opus-5    605     161,601     206,749     11,348    13,991     0.070
  opus-4.8  155     156,549     208,342      9,543    11,488     0.061
  fable-5   124     155,777     168,799      9,970    12,829     0.069

Bogdan's ~30k guess is high; Fablex's "few thousand" is low. **~11k output
on a ~159k context, ratio 6.8%, remarkably tight across models** (6.1–7.0%).
The ceiling is the 64k max_tokens, never approached (max observed 19,858).

## Cost split — the real finding
              n     $ p50    output%  uncached-in%  cache-read%
  WARM      244    $0.3932    72.5%       2.9%        20.7%
  COLD      631    $1.0320    27.5%      71.3%         0.9%
Total $838.68: output $270.77 (32%), UNCACHED INPUT $516.78 (62%).

**72% of compactions read the window COLD at 1x.** Not idle-driven: 78% of
cold ones fired within 5 min of the previous request. Mechanism: cold requests
carry exactly 2 cache markers (sys1, sys3), warm ones carry 4 — the message-level
breakpoints are gone, so the API has no breakpoint near the history and re-ships
~157k at 1x. `strip_compact_cache:true` on 355/355 cold cases.

This is OUR OWN STRIP_COMPACT_CACHE and its known blind spot
(mem-1788298077596-myki1j): the warmth ledger stamps only each request's FULL
depth, but a compact body diverges from the previous request in its last 1-2
messages, so no depth matches, the gate reads "not warm", and strips the very
markers that would have made it warm. Causality is cold->strip (gate reacts),
but the gate's verdict is WRONG. Previously priced on a 14-day window (~$50);
full corpus says ~$517 of $839 — 62% of all compaction spend, recoverable.

## Where compaction fires (auto-compact disabled; operator decision)
  p0 20,635 | p10 113,610 | p25 144,450 | p50 158,979 | p75 177,422
  | p90 205,219 | p100 504,446
Massed 100–250k (843/875 = 96%) despite a 1M window. Operators compact at a
habitual ~160k, not near any technical limit.

## Post-compaction
First resumed request: p50 43,396 tok (p10 34,200 / p90 54,791), = 0.27 of the
pre-compact window (p10 0.20 / p90 0.37). Summary is ~11k of that, so ~32k is
re-attached files + boot load.
Requests until next compaction: p50 114 (p10 63, p90 181, max 707).

## Item 5 — cheaper-model summariser (mem-1788305565061-x8n0ni)
A/B'd 2026-09-02, 8 captured compacts replayed via :7800, scratchpad only.
Quality fine (0 invented paths/shas/tickets, ~65% path overlap both ways) but
sonnet writes 1.4x LONGER summaries, so sonnet-cold ~= 1.3x native-WARM and only
beats native-COLD (59% opus / 73% fable-5-1). PARKED because truly-cold compacts
were then thought ~21% of the fleet and it needs the same warm/cold predicate
STRIP_COMPACT_CACHE was getting wrong. Bogdan's recollection ("only worked for
cold fables") is right in shape.
NOTE: this measurement now needs revisiting — it assumed cold compacts were 21%;
the full corpus says 72%. The downshift's addressable share is 3.4x larger than
when it was parked.

## For the glossed-index idea
Output is 32% of compaction spend and ~11k tokens. An index replacing prose
attacks that 32%; it does NOT touch the 62% uncached-input, which is a
BREAKPOINT-PLACEMENT bug and fixable independently and sooner. Also: the
post-compact window is 43k of which only 11k is summary — shrinking the summary
to ~2k moves the resumed window 43k->34k (-21%), not to zero.
