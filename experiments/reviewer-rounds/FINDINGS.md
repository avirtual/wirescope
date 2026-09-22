# Reviewer seats: where the round trips go, and what would remove them

Corpus: every `clodex.tNNN.review-rR` session on the live proxy in the last 10 days.
225 opus-5 sessions, 118 tickets, $576. Tool roster Glob/Grep/Read only. Method and scripts: this directory's README.
All counts below are from forwarded request/response bodies on the wire, not from CLI transcripts (see README on why the transcript misreads batching).

## Shape of a review

- Median 19 API requests, 28 tool calls in 16 tool-bearing rounds, 8 minutes, $2.13. p75: 26 requests / 40 calls / 13 min / $3.
- Tool execution is ~0s. Round-trip latency median 19s, p75 48s: wall time is `rounds × ~30s`. One removed round ≈ 30s and ≈ $0.11.
- Batching already happens: 70% of tool rounds carry 2+ calls; modal round is a pair. 62% of grep patterns are hand-built alternations of 2–8 symbols. A "batch harder" instruction will not move anything.
- Every session's first request is a 429 (224/225): a 1-message, 0-tool, max_tokens=1 probe the CLI fires on spawn, retried ~1.6s later. Free, but one guaranteed wasted round trip per spawn, in the CLI not the prompt.
- The window at verdict time is 80% tool results (median 83k tokens). Output tokens (80% of them thinking) are 43% of cost. This is an output-dominated workload; a cheap-read model does not help, rounds do.

## What the calls are for (classified against the diff each seat was reviewing)

| Kind | Calls | Definition |
|---|---|---|
| Symbol-usage greps | 1,453 (43% of greps) | Every identifier in the pattern is one the diff added/removed, searched outside the touched files: "who else calls this" |
| Hunk-context reads | 910 (27% of reads) | Windowed Read of a touched file overlapping a diff hunk: re-opening the change to see surrounding lines, because the .diff has 3 lines of context |
| Meta reads | 430 | SPEC.md, JOURNAL.md, prior verdict, the message file. Always the first 1–2 rounds, serialized before any real work |
| Empty/failed greps | 320 (9.5% of greps) | Returned nothing or errored; 133 of the empties are symbol-usage greps confirming "no other callers" |
| Everything else | ~3,600 | The reviewer following its own reasoning |

28% of tool rounds consist entirely of the first three kinds; another 32% partially. Removing only the fully-derivable rounds: median 16 → 11.
Hot files: `team-tickets.js` (542 KB) read 554 times, 552 of them windowed crawls; `renderer.js` (281 KB) 176 times.

## Why tickets need more rounds (62 REWORK verdicts, 106 MUST-FIX items, classified by a sonnet pass)

| Category | Items | Lead item of a verdict |
|---|---|---|
| PROSE: comment/doc/CHANGELOG sentence the code no longer backs | 57 (54%) | 31 |
| LOGIC | 33 (31%) | 25 |
| TEST-WEAK (passes for the wrong reason) | 10 | 4 |
| TEST-MISSING | 3 | 2 |
| SCOPE/PROCESS | 2 | 0 |

Rounds ≥2 (27 items): 15 are NEW defects introduced by the previous round's fix (mostly a fix falsifying a neighbouring comment), 8 are incomplete fixes, 4 are reviewer misses from round 1.
Rounds ≥2 are 50% of reviewer spend ($289). Later-round diffs are cumulative: median 15% of change-lines are new since the previous round, yet a round-2 review costs more than round 1 ($2.69 vs $2.13, same call count) because the scope hands over the full diff as "the authoritative statement of what changed".
Recurring: `team-tickets.js` in ~18 verdicts, `voice-submit-watcher.js` in ~20, CHANGELOG.md as the site of a false user-facing claim in ~6.

## Recommendations, cheapest and largest first

1. **Stale-prose pre-pass before an opus reviewer is spawned.** Diff-only, sonnet/haiku or a hand-side checklist step: "does every comment/doc line in or adjacent to a changed hunk still state something the code does?" Pre-empts ~half of REWORKs and most rounds ≥3. This is where the $289 is.
2. **Delta-scope rounds ≥2**: `prev-round-HEAD..HEAD` plus the open MUST-FIX list, cumulative diff available on request. Safe on the numbers (4/27 later-round findings were round-1 misses).
3. **Spawn-time packet**, no new tool: diff materialized with `git diff -W` (or `-U25`); a usage index for every changed symbol (`grep -n` repo-wide excluding touched files, capped per symbol, "no callers" stated explicitly); SPEC.md and the prior verdict inlined rather than pointed at. Goes in at turn 0 and is cached for the session, replacing the same bytes that today arrive as uncached tool results. Median rounds 16 → 11.
4. **A `usages(symbols[])` tool** only if the alternation-grep pattern persists after 3.

Guardrail: A/B on round-1 REWORK rate and MUST-FIX count, never on round count alone. A cheaper reviewer that misses things is a more expensive ticket.

## Addendum: diff context width, measured (117 tickets regenerated at the merge's branch tip)

| Flag | Size vs `-U3` (median / p90 / max) | Absolute (median / p90) |
|---|---|---|
| `-U15` | 1.66× / 2.24× / 2.9× | 25 KB / 105 KB |
| `-U25` | 2.11× / 3.19× / 4.2× | 31 KB / 133 KB |
| `-W` | **9.2× / 47× / 182×** | 211 KB / **1.1 MB** |

`-W` is unbounded and this repo's hot files have functions hundreds of lines long: t571 goes 148 KB → 2.1 MB. It would exceed the entire tool-result budget of a median review (152 KB) several times over. **Use `-U25`** (or `-U15`): ~10k tokens, cached at turn 0, against a median 50 KB of tool results per session (35% of all tool-result bytes) that the packet replaces.

Process facts from clodex (source-traced): the diff is built at `git-worktree.js:406` with `['diff','--text','--no-ext-diff', base..head]`; the argv is pinned by `test/diff-argv-single-source.test.js` and quoted in two `fail()` messages, so a width change is the leaf line plus those two strings. A second consumer (`_mergeTouchedChangelog`) greps the same text for a CHANGELOG header and has a 32 MB `maxBuffer` that fails closed to `known:false`. The pre-review gate `_verifyTicket` has five machine-decidable CHECKs and no prose check. Rounds ≥2 reuse the ticket's original `baseSha` (never advanced, not a deliberate choice); prior rounds' diffs are kept on disk by round-numbered filename, so delta-scoping needs only a different base.
