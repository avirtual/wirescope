# Reviewer seats opus-5 → fable-5-1 (switched 2026-09-05 ~16:00), first 6 fable seats vs 38 opus seats (30h window)

Scripts: rev2.py / rev3.py (per-seat table), rev4.py (per-request usage), cf.py (counterfactual: mid-turn thinking strip OFF).

| per seat, median | opus-5 (38) | fable-5-1 (6) |
|---|---|---|
| tool-carrying requests | 18 | 5 |
| tools per request | 1.7 | 3.7 |
| wall time | 13.4 min | 3.2 min |
| final window | 91.7k | 86.9k |
| output tok | 57k (thinking 45k) | 14k (thinking 8k) |
| verdict length | 4,036 ch | 4,019 ch |
| cost | $2.90 | $2.87 |
| cost split read/write/out/uncached-1x | 24/18/42/15 % | 2/39/27/32 % |

Same work shape (same distinct-file counts, same verdict format and file:line density), 3.6× fewer round trips, 4× faster,
same dollars despite 2× list price. Verdicts 4 ACCEPT / 2 REWORK (opus 22/16) — n too small to judge quality.

ANOMALY: 32% of fable reviewer spend is UNCACHED 1× input (opus: 15%). Cause = the mid-turn thinking strip + its marker gate.
The gate pulls the rolling tail marker below the last assistant message's live thinking, so each round's new tail (tool results)
is read at 1× this round and written at 1.25× next round (wire: t676 seq15575 in=42,800 → seq15576 w5=42,748 — the 89 KB diff
paid twice). That is the price of never caching a doomed thinking block; it pays off only when the thinking is a large share of
the round's tail. Opus reviewers: 45k thinking/seat, 5.4k tail/round → neutral (−1%). Fable: 8k thinking/seat, 17k tail/round
(fewer rounds, 2.2× more tools per round) → the strip COSTS +26% ($3.84 of $14.89; $0.70/seat median; 22–29% on every seat).
Counterfactual model (cf.py): stock CLI marker on the tail, write new tail once at 5m premium, read at 0.1× thereafter,
thinking retained. Checked against the wire pattern above.

Recommendation: do not opt fable reviewer seats into strip-thinking (clodex: no `[wirescope:strip-thinking on]` for fable
reviewer template), or give the mid-turn strip a per-round economics gate (skip when live thinking < ~X% of the new tail).
Expected: fable reviewer ≈ $1.85/seat vs opus $2.90.

Not model-related: every reviewer seat opens with a 429'd `{"quota", max_tokens:1}` probe (clodex quota probe), both models.

## Strip level, cross-priced (cf2.py, 2026-09-05 evening; 38 opus + 7 fable seats, tool-carrying requests only)

Reviewers are single-user-turn sessions (833/859 requests), so the settled prior-thinking strip never fires; "strip level" on a
reviewer == the MID-TURN strip + marker gate. Its trade per round: save writing T (thinking) once + re-reading it k more rounds,
pay reading the new tail S uncached once. Break-even T/S = in / (write + read·k):

| remaining rounds k | opus-5 | fable-5-1 |
|---|---|---|
| 3 | 0.65 | 0.75 |
| 6 | 0.54 | 0.71 |
| 12 | 0.41 | 0.65 |
| 24 | 0.27 | 0.54 |

Fable's read is half price, so the "avoid re-reading thinking" half of the benefit is worth half as much, while the cost side
(uncached tail at `in`) doubled with the write side. Fable needs ~1.5-2× the thinking density opus needs.

Behaviour × price table (strip ON cost vs OFF):
- opus behaviour (T/S p50 0.35): opus table −1% | fable table +7%
- fable behaviour (T/S p50 0.11): opus table +28% | fable table +34%

Both factors go the wrong way, behaviour dominates: even at opus prices fable-the-model loses 28% to the strip because it
thinks ~1/3 as much per round and ships ~2× the tail. Opus reviewers at opus prices were never a win either (−1%, i.e. a wash);
the only opus seats that gain are the long ones (t656 73 req −10%, t655 r2 −9%) where k is large. Every fable seat loses 17–41%.

Verdict: strip level buys reviewers NOTHING on opus and costs 34% on fable. Set fable reviewer seats to L0 (or gate the
mid-turn strip on T/S ≥ ~0.7). Expected fable reviewer cost $12.18 vs $16.28 for these 7 seats.

## Main clodex seat (long-lived, 1h TTL): strip stack ON vs OFF, per model phase (main_cf.py, 2026-09-05)

Session 5383fbbc ran opus-5 (09-04 08:13→23:27, 704 req) then fable-5-1 (09-04 23:29→09-05 18:15, 836 req) — same seat, same
CLAUDE.md, same work. Counterfactual OFF = thinking + L2-stubbed bytes retained, stock rolling marker (new tail written once at
1h premium, read at cache_read after), cold requests keep the API's real hit prefix.

| phase | req | ON | OFF | ON/OFF | window p50 ON→OFF | midturn think/tail per round |
|---|---|---|---|---|---|---|
| opus-5 | 704 | $67.41 | $74.83 | −9.9% | 120k→140k (+16%) | 1,977 / 1,170 |
| fable-5-1 | 836 | $84.64 | $90.60 | −6.6% | 120k→139k (+16%) | 2,800 / 1,391 |
| (37464a95 opus, 2,222 req, 150 cold restarts) | | $236 | $334 | −29.4% | 132k→155k | |

Cross-priced: fable-phase behaviour at opus prices = −10.6%; opus-phase behaviour at fable prices = −5.3%. So on the main seat
the price table costs ~4 points of the strip's benefit, behaviour is the same (fable thinks as much per round as opus here:
18.8k vs 19.8k stripped tok/req). The strip STILL WINS on fable, −6.6%, and more on sessions with many 1h expiries
(the cold re-write shrinks with the window: 37464a95 −29%).

Mid-turn component: think/tail per warm round ≈ 2.0 on the main seat (tail per round 1.2–1.4k, tool results are small)
vs 0.11 on fable reviewers — far above the 0.75 fable break-even, so the marker gate is a win here too.

L2 isolated (edit-ack collapse + tool-error stubs; fold retired): fable phase reclaimed 1,224 tok/req = 1.0% of read,
556 fires, ≈ $0.26 over the whole phase; opus phase 114 tok/req ≈ $0.04. ZERO warm-prefix busts attributable to L2 (0
unexplained warm writes in either phase). L2 is neither worse nor meaningfully better on main seats: ~0.3% of spend, no cost.

Short fable sessions: 893a6ec8 (45 req, 7 turns, 25k think/round) +13.6% at fable prices (−1.2% at opus prices) — the marker
gate's uncached tail ($2.12) exceeded the read saving ($0.67) because the session was too short to amortize; 30155aba (14 req)
−12.5%. Same lesson as reviewers: the strip's value grows with rounds-remaining; fable's cheap read halves the payoff.

Verdict: keep L2 on the main clodex seat on fable (−6.6% net, L2 itself ≈ free). Turn it off only for short/ephemeral fable
seats (reviewers, one-shot subagents) where the tail dominates the thinking.

## Hands: cost trend 14d + dead roster (hands_trend.py, 2026-09-05 evening; 192 hands, $1,467, 15,700 requests)

Mean $/hand: 08-26..28 $3–6 → 08-29..31 ~$10 → 09-01..03 $5–7 → 09-04 $19 / 09-05 $11.5. Decomposed: $/request rose
0.076→0.097–0.114 (+25–50%) because the window read per request rose 74k→120–154k; requests/hand rose 41→90–168 (2–4×).
Boot load flat 30–35k, lead turns/hand flat ~9–10 → the growth is inside the turns (more Bash rounds: 37→76–110/hand,
edits still via heredocs), partly ticket size (t654 403 req $49, t673 450 req $51 on 09-04). Cannot split ticket size
from behaviour without a work unit (see recon5 lesson).

Agent/SendMessage usage: 24 Agent calls in 9 of 192 hands; 13 of 14 SendMessage calls in ONE hand (t559, 08-29, which
also spent $42 in subagents; t560 $14 — those two are $56 of the $64 total subagent spend). Since 09-04: zero. ListAgents
and SendFeedback: zero ever. Dead roster on hands = Agent 3,691 + SendMessage 6,660 + ListAgents 1,196 + SendFeedback 5,587
= 17.1k ch ≈ 6.3k tok/request → ≈$50 read carriage /14d (3.4% of hand spend) + ≈$12 cold writes. Trim via the hand
template's tool list (or `[wirescope:strip-tools ...]`).

Per-ticket cost (hand + all review rounds, 83 tickets): $14.69 mean = $9.71 hand + $4.98 review (34%).

## Hands: WHY the cost jumped (hands_why*.py + merges.json, 2026-09-05 late; 186 hand-tickets joined to first-parent merges on wb-wrap-ui master)
Verdict: **ticket size, not behaviour.** Normalized by changed lines in the merged ticket, cost is FLAT across the jump:
$/100 changed lines = 2.02 (≤08-28, 69 tk) · 1.96 (08-29) · 1.83 (08-30..09-03, 75 tk) · 2.05 (≥09-04, 32 tk); req/100 changed lines FELL 24.8 → 17–20.
What grew is the work unit: changed lines/ticket 224 → 414 → 678, files/ticket 5.7 → 6.9 → 12.4 (09-04: 20 files/tk, four tickets >1,600 ins).
- **08-29 step** = the decommenting day: 10 single-file tickets, 3,647 deletions vs 1,577 insertions, `## Comments` section added to the hand prompt that day, sweep greps 7.6/hand. Same $/line.
- **Bash-first is NOT the cause of THIS jump**: the bash-first paragraph is on 100% of hands since 08-18 (before the cheap week). `COZY_TEAPOT=relaxed` shows up on 09-05 (24/25 hands) and Edit usage came back (27/hand vs ~0–10) — too early to price.
- **Prompt compliance is real but ~10%**: hand role prompt 26.4k → 32.4k ch (+23%: Comments, decommenting sweep, hunk-review with 25 ctx, red-proof, last.txt rule). Compliance bash calls (comment-ratchet/boundary-check gates, sweep greps, `git diff -U2x+`, stash/restore red-proof) rose from 1.5–3% to 13–20% of bash calls = ~11/hand on 09-05, ~21/hand on 09-04 → ≈$1–2.5/hand of $11–19. Result volume of those calls 17–19% of tool-result chars.
- Rework share (tools after first task-done) 19–31%, no trend. Test-failing runs 6–9/hand on 09-04/05 vs 1–4 early (bigger tickets, more gates).
- Spec size flat (~4–6 KB), lead turns flat, replays flat → not "the lead talks more".

## First fable HAND: t680 vs same-day opus hands (hand_cmp.py, 2026-09-05 20:00)
t680 (fable-5-1): 1,071 changed lines / 25 files, **58 req, $10.96, 21 min**, 77 tools (1.33/req; survey batched 7/5/3/3), 0 Edit — one python heredoc per file applying several replacements (10 scripts), 14 test runs (4 red), review r1 ACCEPT no must-fix.
Size twin t676 (opus-5, 1,200 lines / 26 files): 190 req, $18.50, 26 min, 200 tools (1.05/req), 42 Edit one-at-a-time, 36 test runs (13 red).
Per 100 changed lines: t680 **5.4 req / $1.02**; the 8 opus hands of 09-05: 16–37 req / $1.28–3.69 (median ≈ 20 req / $2.4). Fable ≈ 3–4× fewer requests, ~half the $ per line, despite 2× out price (out $3.78 of $10.96). Same t680 pattern priced at opus rates ≈ $8.26 → behaviour saves more than the price table costs.
Cost structure flag: write+uncached-in = $5.34 (49%) on t680 (w1h 187k @$20, in1x 159k @$10) = the mid-turn strip + marker gate on fable at 1h; L0 counterfactual ≈ −$0.9 (−8%; thinking only 21.8k). n=1; relaxed bash-first was on (09-05 hands all had it) yet fable still routed edits through python heredocs.

### t679 (opus) vs t680 (fable), same evening
t679: 2,443 changed lines / 32 files (2.3× t680), 292 req, $40.02, 54 min, review REJECT → rework round (36 asst msgs, ~12%). Per 100 lines: 12.0 req / $1.64 (t680: 5.4 / $1.02) → 2.2× requests, 1.6× $ per line; t679 is among the MORE efficient opus hands per line (opus median ≈20 req/$2.4).
Where the $ is: cache READ $31.23 of $40.02 (62.5M read tokens: 292 req × mean window 214k; t680 7.4M = 58 × 131k) — the read line is 17× t680's. Requests: 173 reads (136 bash + 16 Read, 8 rereads), 49 Edit (676 ch each) + 33 heredocs (2.5k ch) vs t680's 17 heredocs at 4k ch; 45 test runs over 26 distinct invocations (12 red) vs 14 over 8; batching 5% vs survey batches of 3–7.

### t681 (sonnet-5), the first sonnet hand — 2026-09-05 20:24→20:59

Ticket = carried-nits sweep (14 reviewer nits from t673..t680), merge 427+/169− = 596 lines / 23 files. Review r1 ACCEPT, no must-fix (fable reviewer).
384 req, $17.03, 35 min wall (1 gap >2 min). 380 tool_use, 0.99/req, 0% batching: Bash 225 (grep 74, sed-n 24, test 38 of which 13 red, git 22, other 67), Read 79 (70 windowed with offset/limit), Edit 75 (checklists.js 11, team-tickets.js 9), Write 1. 10 commits, one per nit/pair.
Window grew monotonically 1k→305k with NO compaction (1M model), mean window per quarter 77/146/216/283k; cost per quarter $2.33/3.54/5.19/5.97. Read line $13.83 = 81% of bill on 69.2M read tokens (MORE than t679's 62.5M at 292 req). write $1.23, out $1.61 (161k out, 71k thinking), in $0.35.
Per 100 changed lines: 64 req / $2.86 / 64 tools — worst of the four (t680 fable 5.4/$1.02, t676 opus 15.8/$1.54, t679 opus 12.0/$1.64). Same token stream at opus prices ≈ $42.6 (read $34.6): sonnet's 0.2 read rate hid a request pattern ~2.5× worse per line than opus. Caveat: a 14-nit sweep is inherently more scattered than one feature, so part of the per-line penalty is ticket shape; the per-request pattern (one grep, one sliced Read, one Edit, next request; single-file test runs with tail) is the model.

## Agentic hand prompt (09-06 opus, role f22814f6 35,772 ch) vs old prompt (09-05 opus, ee37a6b4 32,410 ch)

Prompt diff: (1) DELEGATE lookups (Explore) and red-proof verify loops (general-purpose) to subagents, keep edits/commits; (2) single test file may run in the worktree (old prompt forbade any bare `node --test`), full suite still via granted command; (3) compact on a rework that arrives past ~150k (journal first); (4) commit before red-proof (hand-673 lost work).

Cohorts (merged tickets only, subagent $ folded in): OLD n=20, 11,949 lines, $270; NEW n=13, 6,215 lines, $134.
- $/100 changed lines: median 2.25 → 1.88 (−16%), pooled 2.26 → 2.16 (−4%); excluding t685 (the one rework+double-compact ticket) pooled 1.81 (−20%).
- req/100 lines: median 28.0 → 23.8, pooled 22.0 → 23.3 (flat).
- Mean window per request: median 83k → 97k (NOT smaller — the hand's own context did not shrink; delegation moved reads out but single-file tests + subagent reports came in).
- Wall min/100 lines: 5.6 → 4.4 (−21%).
- Review: non-accept 9/20 → 2/13; mean rounds 1.45 → 1.15. Reviewer changed too (09-05 half opus/half fable; 09-06 all fable) so partly confounded, but fable reviewers on 09-05 rejected at the same rate as opus.
- Subagent spend $6.78 (5.1% of NEW bill) — 7 of 13 hands delegated; red-proof reverts $0.1–0.5 each on 20–30k windows. t694 delegated test-WRITING to a 46-req/$2.78 subagent (prompt says never delegate an edit) — one violation.
- Compactions: t685 compacted twice (199k→19k at rework arrival, 238k→19k 17 min later); still $36.89/360 req for 932 lines — the rework rule fired as written but the ticket was already expensive.
Verdict: modestly better per line (−4% pooled, −16% median, −20% excl. the one rework blow-up), faster, fewer rework rounds; the hand's window did not get smaller. Ticket mix (median 338 vs 376 lines, similar) is comparable enough for a directional read; n=13.
