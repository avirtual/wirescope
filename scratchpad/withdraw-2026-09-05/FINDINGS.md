# Model-initiated tool-result withdrawal (`[wirescope:withdraw <tool_use_id>]`) — priced 2026-09-05, NOT BUILT

Bogdan's hypothetical: let the model flag the previous round's tool result as a miss, and have wirescope stub that `tool_result` body on every later request (persisted by id, re-applied deterministically, like the strip-guard latch). Design was worked out and is sound: response-side parse in `receipts.anthropic` off the full SSE blob (not the 4k-capped text), previous-round-only validation against the forwarded body's last user message, a `withdrawn_results` table via the store registry, a `_stub_withdrawn_results` transform in the L2 slot ahead of the compact-cache strip and the settled pin, kill-switch in the WS_OMIT style, phase two = apply the set in the `/_compact` bake so a resumed CLI ships the stub itself. Roughly 200 lines. Clodex half = one line of hand role prose.

## Why it was not built: the ceiling is ~3.5% of the hand bill at PERFECT compliance

Corpus: every clodex hand seat's LAST request (305 seats with ≥10 messages, 2026-07-08 → 09-05), receipts for the seats' full bills ($2,298.62), prices from `proxylab.billing`. A result is "dead-end" if nothing the model said or called afterwards references it (read: the path's basename never recurs in any later assistant text or tool input; search: no path in the output and not the pattern recurs). Saving = body tokens × requests remaining after the withdrawing round × cache-read price; cost = one-time re-write of the withdrawing round's assistant message + stub at the 5m write price.

| class | count | rate | carriage | net USD | share of bill |
|---|---|---|---|---|---|
| dead-end reads (Read + bash cat/head/sed) | 450 / 5,270 | 8.5% | 552k tok | | |
| dead-end searches (Grep/Glob/bash grep/rg/find) | 2,336 / 6,018 | 38.8% | 761k tok | | |
| both, perfect withdraw | 2,786 | | 1.31M tok (5.2% of message bytes) | +$39.16 − $6.97 bust = **$32.19** | **1.40%** |
| + wrong-chunk reads (same file re-read within 2 rounds, other range) | 855 | 16.2% of reads | 1.33M tok | +$49.09 | → **$81.28 combined, 3.54%** |

Per seat: $0.11 net at perfect compliance. Per-seat dead-end read rate p50 9%, p75 15%; 26% of dead read tokens sit in the fattest 10% of results, so no "just the big ones" shortcut either. Searches are dead 4× as often as reads but are small (p50 154 tok).

Realistic compliance is a fraction of perfect (the model must notice, must be right, must remember the sentinel; SC needed a blunt prohibition + exemplar to get sonnet to 3/3 and opus/haiku deferred), and a wrongful withdrawal costs one re-read round trip ≈ $0.12 on a fable window, i.e. ~1.5 correct withdrawals. Against strip-thinking's 16–22% and tool-trim's −72.7% tokens this is noise with a foot-gun attached.

## The no-cooperation alternative is also dead

Exact same-range supersession (same path, same `offset/limit` or `sed -n a,bp`, no intervening edit, read again later — the only case that is safe by construction): **2 of 762 keyed reads**, 213 tokens. Models never re-read the same range; they re-read a different range or the whole file, which does NOT semantically supersede the earlier chunk. Loose "same file read again later" is 2,388 reads / $127.60 (5.6%) but that is not a stubbable class. Open item (c) tier-2 supersession stubs on reads have no payload; leave (c) to edit-acks and bookkeeping.

## Durable lesson

Tool-result carriage is where the *bytes* are (40% of message bytes), but the *waste* inside it is thin and evenly spread: single-digit percent dead, no fat tail, and the only stubbable-by-construction subclass is empty. Same shape as the redundant-read verdict (1.75%, `/_pot` retired) — file-level waste theories keep coming back ~2%; the levers that moved 15%+ were all PREFIX shape (tools, thinking, boot load) and route regime (bash-first), not per-result surgery.

Scripts: `deadend.py` (reads), `deadend2.py` (+ searches, wrong-chunk, size distribution), `ceiling.py` (USD, receipts-priced), `supersede.py` (exact supersession).
