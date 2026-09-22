# clodex coordinator seat: opus-5 vs fable-5-1 (2026-08-20 .. 2026-09-06)

Scripts: `extract.py` (one pass over the capture root -> `main_seat2.jsonl`, per-request receipts, repriced from the current PRICES table, strict compact detector from compaction-2026-09-04), `analyze.py` (phases, turns, compact epochs, intents), `writes.py` (write-side decomposition + TTL counterfactual).
Population: agents matching `clodex-clodex-<8hex>` on the main line (role=parent), cwd wb-wrap-ui. Hands (`clodex-clodex.tNNN.hand-*`) and reviewers excluded. Hold pings (1,261 req, $32) excluded. Sessions: d48e988f, 0731c55e, 4d74dc16, 37464a95, 7a549dc5 (opus) and 5383fbbc (opus 09-04 08:13..23:27, then fable from 09-04 23:29).

## Cohorts
The switch coincided with two other regime changes, so the honest opus control is NOT the 2-week total:
- v0.6.58 (STRIP_COMPACT_CACHE retired) went live ~09-03. Before it, 69/107 opus compacts were COLD (window re-shipped at 1x, $1.035 p50). After it, 0/13 opus and 0/19 fable compacts are cold.
- The same seat (5383fbbc) ran opus for 15h on 09-04 and then fable: same day, same operator, same ledger state. That is the tightest control.

| | opus ALL (08-20..09-04) | opus post-09-03 | opus same seat 09-04 | fable-5-1 (09-04 23:29..09-06) |
|---|---|---|---|---|
| requests / turns | 13,131 / 3,055 | 1,416 / 337 | 703 / 175 | 2,034 / 715 |
| active hours | 207 | 29.5 | 13.6 | 40.7 |
| total $ | 1,400 | 144.16 | 67.25 | 226.37 |
| $/request mean (p50) | 0.1066 (0.094) | 0.1018 (0.092) | 0.0957 (0.088) | **0.1113 (0.081)** |
| $/turn mean (p50) | 0.458 (0.285) | 0.428 (0.282) | 0.384 (0.240) | **0.317 (0.167)** |
| req/turn | 4.30 | 4.20 | 4.02 | **2.84** |
| $/active hour | 6.75 | 4.88 | 4.94 | **5.57** |
| $/coordinator intent | 0.583 | 0.577 | 0.666 | **0.583** |
| window/req p50 | 126.8k | 123.8k | 120.4k | 122.3k |
| output tok/turn | 3,404 | 3,585 | 3,000 | 2,154 |
| visible text chars/turn | 2,494 | 2,632 | 2,368 | 1,144 |
| cross-priced at the other table | 1.13x | 1.12x | 1.08x | 0.90x |

Per request fable is +9..16% dearer; per turn it is −17..26% cheaper because it needs 2.8 calls per turn instead of 4.0..4.3. Per coordinator intent (dm / task verbs / remind / memory / notify-user = the seat's actual work product) it is a wash: $0.58 vs $0.58 (vs $0.67 on the same-seat opus day). Per active hour it is +13%, and the seat also delegates more (subagent line $10.44 = 4.6% on top vs 1.3% on opus).

Fable writes less than half the prose per turn (1,144 vs 2,632 chars) and spends fewer output tokens per turn (2,154 vs 3,585) despite output being priced 2x — that is where the per-turn win comes from.

## Where a fable request's money goes (per request, 1h TTL)
| component | opus post-09-03 | fable | delta |
|---|---|---|---|
| cache read | 119k tok x $0.50 = $0.060 | 120k x $0.25 = $0.030 | −$0.030 |
| cache write (1h) | 1.7k x $10 = $0.017 | 1.8k x $20 = $0.036 | +$0.019 |
| output | 853 x $25 = $0.021 | 757 x $50 = $0.038 | +$0.017 |
| uncached input | 716 x $5 = $0.004 | 742 x $10 = $0.007 | +$0.004 |
| **total** | **$0.102** | **$0.111** | **+$0.009** |

The read discount is real and worth $0.030/request, and writes + output give all of it back. Fable's 1h write rate is 80x its read rate (opus: 20x), so everything that WRITES is now the bill: write is 32% of the fable phase (16% on opus), output 34% (19%), read only 27% (59%).

Write-side decomposition (fable phase, $73.29 of $226.37):
- normal tail writes: 1,991 req, 2.61M tok, $52.20 (23% of phase; opus equivalent 11.5%)
- post-compact re-cache (first request after a compact, ~27k tok each): 19 req, $10.42 (4.6%)
- busts (write >20k): 5 req, $10.38 (4.6%) — two `bust:system` at $3.38 + $2.69 (167k and 134k re-writes), one `preamble` $1.87, two `tools` $1.39 + $1.14. Each bust costs 2x what it did on opus.
- turn-opening request $0.117 vs continuation $0.108 on fable; on opus 0.123 vs 0.095 (the turn-opener carries the big tool-result write).

TTL: 5m would NOT be cheaper on either model (6.5% of gaps are >5 min; fable net −$159 if switched to 5m). 1h stays.

## Compaction
| | opus ALL | opus post-09-03 (warm) | fable |
|---|---|---|---|
| compacts | 107 | 13 | 19 |
| $/compact p50 | 1.035 (69 cold) | 0.364 | **0.531** |
| context in p50 | 196k | 184k | 183k |
| summary out p50 | 10,394 | 10,380 | 9,044 |
| output share of compact $ | 30% | 71% | 85% |
| requests per epoch (between compacts) p50 | 119 | 96 | 106 |
| turns per epoch p50 | 24 | 23 | **36** |
| intents per epoch p50 | 21 | 16 | 16 |
| $ per epoch p50 | 12.09 | 10.10 | 10.55 |
| wall hours per epoch p50 | 1.67 | 1.63 | 1.87 |
| window after compact p50 | 53k | 56k | 54k |
| window at compact p50 | 195k | 182k | 181k |
| compact $ / (epoch $ + compact $) | 7.1% | 3.6% | **4.9%** |

A warm compact on fable costs $0.53 vs $0.36 on opus (+46%): the summary is 13% shorter (9.0k vs 10.4k tok) but output is priced 2x, and 85% of the compact bill IS the summary. Add the post-compact re-cache (54k fresh tokens at $20/MTok = $1.09 vs $0.56) and a full compaction cycle on fable is ~$1.6 vs ~$0.9. Epochs are the same length in requests (~100) and dollars (~$10.5), but hold ~50% more turns (36 vs 23) because fable uses fewer calls per turn. The pre-09-03 opus number ($1.035, 7.1% of spend) was the STRIP_COMPACT_CACHE defect, not opus.

## Verdict
- **Per unit of coordination work, cost is unchanged** ($0.58/intent both). Per turn fable is ~20% cheaper, per request ~10% dearer, per active hour ~13% dearer (it moves faster and delegates more).
- **The fable price table inverts the levers.** Reads are now nearly free (27% of bill); writes and output are 66%. On this seat every marginal token WRITTEN (tail, re-cache, bust) costs 2x opus, every token READ costs half. The proxy's strip stack still nets −6.6% on this seat (HANDOFF 09-05), but anything that causes a re-write (system-prompt edits, tool-roster changes, CLAUDE.md edits mid-session) now costs $2-3.4 per event; five busts were $10.38 = 4.6% of the fable phase.
- **Compaction itself is ~5% of fable spend** (was 3.6% on warm opus); the cycle incl. re-cache is ~$1.6. At ~19 compacts per 2 days that is ~$30/2d. Not the lever. Longer epochs would trade against the >200k surcharge question still open from 09-02.
- Numbers are two days of fable (2,034 requests) against 15 days of opus; the same-seat control is one day. Re-run `analyze.py main_seat2.jsonl --since 2026-08-20` after another week.

# Addendum 2026-09-07: what ENTERS the fable seat's window, by source

Scripts: `inbound.py` (diffs consecutive request bodies of one agent hash, classifies every appended block by source; captured bodies are post-transform so it strips the proxy's trailing hint before aligning), `inbound_report.py`. Fable phase = 2,083 requests / 21 compaction epochs; opus controls = same seat (704 req) and 37464a95 (4,512 req).

Growth per delta request is identical across models: p50 ~645 est tok, mean ~950, one new user message per request. Receipts say 2,561 written+uncached tok/request on fable; the classifier sees ~49% of that (JSON framing, ids, thinking that the strip removes before capture).

## Fable phase, organic growth (delta requests), share of appended tokens
| source | est tok | share | $ write + re-read |
|---|---|---|---|
| seat's own output (tool_input 449k, text 238k, thinking 179k) | 865k | 40% | 20.56 |
| Bash/Read tool results (not dm/task-file reads) | 590k | 28% | 14.02 |
| task/state files read (live.md, DECISIONS.md, JOURNAL) | 317k | 15% | 7.53 |
| peer dms incl. auto-read attached bodies | 189k | 9% | 4.50 |
| CLI injections (rosters, system reminders, memory attach) | 112k | 5% | 2.67 |
| task board / ticket-loop notices | 37k | 1.7% | 0.87 |
| operator text | 11k | 0.5% | 0.27 |
| reminders | 4k | 0.2% | 0.09 |
| intent acks | 2k | 0.1% | 0.05 |

Opus same-seat control: output 44%, tool results 32%, task files 11.5%, dms 6%. Opus 37464a95: output 51%, tool results 27%, task files 9%, dms 7%. Same shape.

## Intent traffic is NOT the problem
dms + task board + reminders + acks = 11% of growth ≈ $5.5 of the $231 fable phase. dm bodies arrive as a 107-char stub and are auto-read by the CLI as a role:system message (mean 1,098 tok/dm). The intents the seat RECEIVES are already lean.

## Where the growth is: the seat talking to itself through Bash (fable phase, 1.35M tok of Bash traffic)
- DECISIONS.md: 313 heredoc writes (180k tok of command = output AND then cached as tool_input) + 373 read-backs (131k tok). ~15 writes + ~18 reads per epoch ≈ 15k tok/epoch.
- live.md: 137 reads, 86k tok, 6.5/epoch; the "read the NOW block + tail DECISIONS" combo is the top-12 largest single results (5.8–7.3k tok each).
- `sed -n` source reads 219 calls 267k tok; `echo`-labelled multi-command probes 149 calls 224k tok (1.5k/call); `git diff/log` 141 calls 125k tok.
- thinking 179k tok (8%) — carried in the window on this seat because the mid-turn strip only removes prior-turn thinking; it is written once at $20/MTok.

## Levers, in order of tokens
1. Stop re-reading own state files: the seat wrote DECISIONS.md 313 times and read it back 373 times. A write-only journal (or a `tail -5` cap on read-backs) removes ~130k tok/2d of growth plus the re-reads.
2. Cap Bash result size for the coordinator (`| head -40` discipline or a clodex-side truncation at ~1.5k tok for the lead seat): 590k tok of tool results, mean 914/call, p-top 5–9k.
3. Terse tool inputs: heredoc writes to task files are output tokens (2x price) then cached as input. 180k tok of DECISIONS heredocs = $9 output + $3.6 write.
4. dm/task/reminder plumbing: already 11%, nothing to gain there.
