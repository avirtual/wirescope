# Can the reviewer bootstrap buy fewer requests?

**Measured on 65 clodex-reviewer sessions captured on the live wire (2026-07-20 → 2026-08-05), $99.99 of real spend.**
Written for clodex, 2026-08-05, by wirescope.

## Why this exists

Bogdan's intention, to log and explore — not a decision, and not a request to build anything yet:

> The way reviewing works is that an agent is spawned from a template, given a diff, and asked to fix something.
> We need to figure out if there is anything in that bootstrap info that could deliver the same review with less requests.
> The thinking being: if we can anticipate some reads and greps and just give the agent the information, it doesn't have to be an LLM doing that read/grep — maybe the review can be cheaper/faster.
> It doesn't mean we can optimize this, but we should at least explore the possibility.

The hypothesis is specific and testable: **a reviewer's tool loop contains mechanically-derivable work, and moving that work into the bootstrap converts LLM round trips into a script.**
So I tested it against the capture corpus rather than reasoning about it.

**The short answer: the bootstrap idea does not pay, and the reason is not cost — it is relevance.**
Preloads must be tiny to break even (~1k tokens), but every candidate that IS tiny answers a
question the reviewer never asks. The reviewer greps for the pre-existing code a change plugs
into, never for the names the change introduces — and that is a question about the tree, not
about the diff.

**What does pay, and needs no prediction at all: batching (22.8%) + thinking-strip (7.9%) ≈ 30%**
of spend on the sessions that matter.

Read in order: the knife-edge framing below is superseded by §"The design rule INVERTED", which is
itself narrowed by the candidate tests under it. All three are kept because the corrections are
the finding — two of them reversed a verdict.

## How reviews actually run (surveyed, not assumed)

The premise "an agent is given a diff" holds less often than expected:

| shape | n | rounds/sess | calls/sess | $/sess | what it is |
|---|---|---|---|---|---|
| diff-file | 31 | 14.6 | 20.9 | 1.40 | reads a `.diff` artifact from disk |
| diff-inline | 7 | 10.6 | 15.4 | 0.97 | diff text arrives in the brief |
| git-bash | 2 | 20.0 | 22.5 | 2.59 | reviewer generates the diff itself |
| no-diff | 25 | 22.0 | 32.0 | 1.79 | worktree/spec review — Globs a tree, reads plan docs |

**42% of sessions (27/65) never receive a diff at all**, and those run 22.0 rounds vs 14.6 for a diff-file review — 1.5× the round trips.
Any bootstrap change keyed on "the diff" therefore misses about half the population by construction.

Worth knowing: **5 sessions reached for `git diff` via Bash and were refused** — the roster is Glob/Grep/Read in 63 of 65 sessions, so Bash returns `is_error: "No such tool available"`.
8 such calls across 5 sessions, each a fully wasted round trip.
That is a free fix independent of everything else: either grant Bash or tell the reviewer in the brief that it has no shell.

## The economics: what one extra request actually costs

Every round trip re-sends the whole conversation. The marginal cost of one additional round (corpus medians):

| | tokens | × price | $ |
|---|---|---|---|
| read (re-carry the window) | 46,654 | 0.5e-6 | 0.0233 |
| output (that round's thinking + tool_use JSON) | 906 | 25e-6 | 0.0227 |
| write (the round appends to the prefix) | 4,830 | 6.25e-6 | 0.0302 |
| **total** | | | **$0.0762** |

Observed corpus average is $0.0895/round ($99.99 ÷ 1,117 rounds), so **the marginal round explains ~85% of the entire bill.**

The single most useful number here: **~91% of spend is round-coupled.**
Output looks irreducible ("that IS the review") but only the *final* diagnostic is — and that is **8.9% of spend**.
The other 75% of output tokens are emitted by loop rounds (per-round thinking + tool_use JSON), which scale with round count exactly like reads and writes.

So Bogdan's instinct is economically correct: **request count is the right thing to attack.** The disagreement is only about *how*.

## The cohort that matters

Recent reviews are structurally heavier, and Bogdan's call is to optimize for those.
Defined by shape rather than recency so it stays reproducible: **`mean_window >= 60k`** = 21 of 65 sessions (10 from the last 4 days).

**53% of all spend from 32% of sessions.**

| | big-window (n=21) | rest (n=44) |
|---|---|---|
| $ | 53.07 | 46.92 |
| $/round | 0.0988 | 0.0809 |
| read share | 39.6% | 24.0% |
| final diagnostic | **7.3%** | 10.6% |
| batching lever | **22.8%** | 13.6% |
| pre-serve lever (oracle) | +1.1% | +2.0% |
| thinking-strip lever | **7.9%** | — |

## Testing the hypothesis: what fraction of the loop is derivable?

Every one of the 1,600 tool calls was classified by whether a bootstrap script could have answered it in advance:

| class | calls | share | pre-servable? |
|---|---|---|---|
| DERIVABLE — Read overlapping a hunk the diff already showed | 114 | 7.1% | yes |
| MANIFEST — Glob / file list | 82 | 5.1% | yes |
| EXPANSION — Read into a diff file but away from every hunk | 106 | 6.6% | only by shipping whole files |
| **OFF_DIFF — Read/Grep of a file no diff mentions** | **1,153** | **72.1%** | no |
| DISCOVERY — repo-wide Grep, no path | 145 | 9.1% | no |

**The "line 59 changed → Read line 59" reflex is real but small: 7.1% of calls.**
72% of the loop is the reviewer chasing seams into code the diff never mentions — asking "who else calls this", "is this invariant held elsewhere".
That is structurally underivable from a diff: a diff shows one side of every seam it creates, and an exhaustiveness claim ("the only writers are X and Y") cannot come from it by construction.

A supporting data point on staleness, from session `4db1569a`: clodex sent a mid-session amendment telling the reviewer that *"cli/README.md gained a caveat AFTER your diff was cut — read the live file rather than the patch's hunk."*
Preloaded content can be stale in a way a live Read is not. That is a correctness argument, not just an economic one.

## What pre-serving is actually worth — and a correction

**I need to flag an error in my earlier analysis.** I previously reported that pre-serving "doesn't pay, net −$0.16" — but I priced the *saving* as read-only while pricing the *cost* in full.
Since a killed round also removes its output and write tokens, that understated the lever. Corrected, the sign flips:

| | corpus | cohort |
|---|---|---|
| read-only saving (what I first reported) | −1.6% | −2.1% |
| **full round saving (correct)** | **+1.6%** | **+1.1%** |

So it does pay — **but only with a perfect oracle**, and that is the finding that matters:

**Over-ship sensitivity** (preload the right things, but N× more than needed):

| preload size | corpus | cohort |
|---|---|---|
| 1× (oracle) | +1.64% | +1.14% |
| 1.5× | −0.21% | −1.33% |
| 2× | −2.05% | −3.80% |
| 4× | −9.43% | −13.67% |
| 8× | −24.19% | −33.42% |

**The break-even is ~1.35× over-ship.** Guess slightly wrong about what to preload and the lever is negative; guess 4× wrong and it costs more than every other lever combined saves.

The mechanism is the asymmetry in the pricing table above: a preload is written once (6.25e-6) and then **re-read on every remaining round** (0.5e-6 × rounds), while a killed round saves only its own one-time cost.
Formally, pre-serving pays iff `preload_tok × rounds_it_rides < rounds_killed × mean_window`.
Big-and-late loses even when perfectly derivable — and it loses *harder* on exactly the big-window sessions we care about, because the rent scales with window size.

Restricting to the cheap half (file lists only — Globs are tiny) is more robust but smaller: +0.86% on the cohort at oracle, still negative by 2× over-ship.

**This does not close the door.** It sets a precise engineering bar: a preload mechanism must be *targeted*, not generous. "Bundle the files the diff touches" is over-shipping by well more than 1.35× and would lose money. Something narrow — the specific hunk neighbourhoods, a file list, a symbol index — could clear it, but the margin is ~1-2% and the design has to be defended against over-shipping rather than assumed.

## ⚑ The design rule INVERTED (2026-08-05, after two corrections — wirescope + clodex)

Everything above prices preloading by *what it's worth*. That framing is wrong, and two independent
corrections — one from each of us — together flipped it. Neither alone would have.

**Correction 1 (clodex): rent is paid by MISSES, not by preload size.** The formula
`preload_tok × rounds_it_rides` charges full rent to content the reviewer *would have read anyway*.
But that content enters the window either way — the live read puts it there at round 1, the preload
at round 0. Same bytes, same duration. For the median no-diff first read (2,502 tok, 22 rounds):
live $0.0419 vs preloaded $0.0432 — **rent differential $0.0013, not $0.043.** Verified
independently on the wire corpus; reproduces to the digit.

**Correction 2 (wirescope): the hit rate is 24%, not 100%.** The corrected model was still priced
against a perfect oracle. Measured on the 25 no-diff sessions, a preload of the stereotyped first
read (`journal.md`) has **break-even hit rate 45%** — mean gain on a hit +$0.0526, mean cost on a
miss $0.0431 (a miss is pure added context and rides every round; correction 1 does NOT apply to it).
Observed: **6/25 = 24%**, and 6/22 = 27% even within the dominant project. **So that candidate LOSES
money (−1.1% of no-diff spend). Killed.**

**What the pair produced: break-even is a steep function of WEIGHT, and gain-per-hit is nearly flat.**
Miss cost scales linearly with size; the prize doesn't.

| preload size | break-even hit rate |
|---|---|
| 50 tok (a pointer) | **1.6%** |
| 150 tok | **4.6%** |
| 400 tok | 11% |
| 800 tok | **20.5%** |
| 2,502 tok (a plan doc) | 45% |
| 10,000 tok | 78% |

**At the observed 24% accuracy the maximum viable preload is ~978 tokens.** (clodex derives ~849
from Claude transcripts — different corpus, different unit, same order; the gap is immaterial to
the rule.)

**Therefore the question is not "what is the most valuable thing to preload" but "what is the
SMALLEST thing that kills a round" — the opposite question. At low weight, being wrong stops
mattering.** A pointer ("the diff is at X, the journal is at Y, the spec is at Z") is 50–150 tok
and breaks even at 1.6–4.6% — essentially free to be wrong about — and it targets the no-diff
population's actual problem, which is *locating* the plan doc rather than reading it.

**This retroactively explains MANIFEST.** In the original classification it was the only
pre-servable category that ever looked positive, and I read that as a weak version of the
file-content case. It was never that. **Globs are tiny — it was the whole case, and the
file-content framing obscured it.**

### Candidates tested under the corrected rule — all three fail or are marginal

1. **Plan-doc preload (2.5k tok)** — DEAD on hit rate. Break-even 45%, observed 24%. −1.1%.
2. **Pointer pack ("the diff is at X, journal at Y")** — DEAD on RELEVANCE, not weight (clodex,
   68 seats / 1,075 rounds). It breaks even at ~5% so it can be almost entirely wrong and still
   pay — but reviewers don't spend rounds locating *files*: 318 of ~400 locate calls are open-tree
   **symbol** greps, and Glob patterns barely repeat (top is `**/CLAUDE.md`, 4 of 68). No amount of
   cheapness rescues a preload that answers a question nobody asks.
3. **Diff-keyed caller index** — MARGINAL-POSITIVE, not dead. **19 of 268 greps = 7.1%**
   (31 diff-file sessions) are answerable from an index of symbols the diff declares — after
   word-boundary matching, dropping generic names, and hand-auditing the survivors; a raw
   substring pass gave a polluted 16.9%. Worth +$0.027–0.072/session ≈ 2.5–5% of diff-file spend,
   ~1–2% of total. Clear examples: `sanitizeRebootNotice|slice\(0, ?500\)`,
   `deployDockerVerb|deploySsmVerb`, `addSessionToSidebar\(`.
   *An earlier 0-of-17 result (6 commits, scored per-commit) is superseded and was withdrawn by
   clodex: at p≈0.07, 17 draws give zero hits ~29% of the time, so it could not distinguish 0%
   from 7%, and per-commit weighting hides the grep-heavy sessions where an index would pay.*
   **Still not worth building** — 1–2% loses to batching's 22.8%. Caveat: 7.1% rests on a
   19-match hand audit; treat as order-of-magnitude.

**The shared mechanism behind 2 and 3 — this is the durable finding.** The reviewer greps
overwhelmingly for the **pre-existing code a change plugs into**, rather than for the names the
change **introduces** — it already has those, they are in the diff. (The 7.1% above is the
minority tail, so this is a strong tendency, not an absolute.) (clodex reviewing `74f0a87`: grepped `sidebarMeta`,
`metaFor`, `SCANNED_MODULES`; the diff declared `META_TIERS`, `TIER_OF_KEY`, `mergeMeta`. Zero
overlap.) "Who else calls this" is a question **about the tree**, so a diff cannot answer it in the
general case. That is the 72% OFF_DIFF figure seen from the other side, and it means **no preload
keyed on the diff closes more than a few percent of it.**

### Methodological notes — three failure modes, all real, all nearly shipped

Both pricing errors were the same shape on opposite sides of the ledger:

1. **Read-only pricing of savings under-prices anything that removes a round by ~3×** — output and
   write are two-thirds of a round's marginal cost.
2. **Pricing rent without asking whether the content would have been carried anyway over-prices
   every preload** — correctly-predicted content enters the window either way.
3. **A plausible hit rate on an unaudited key set** (clodex: first pass scored 35%, entirely
   pollution from `meta`/`cache`/`assert`/`rows`; filtering took it to 0. wirescope: 16.9% → 9.7%
   → 7.1% by the same audit). **The arithmetic was fine and the inputs weren't** — the one failure
   mode neither pricing correction would have caught. Audit the key set before believing a rate.

## What pays without needing to predict anything

**1. Batching — 22.8% of cohort spend.**
**50.3% of all rounds fire exactly one tool call** (46.2% fire two, 3.5% fire three or more; max observed 5).
Merging pairs of single-call rounds loses no information — it's the same calls in fewer round trips.

The obvious objection is that consecutive calls may be causally dependent, so I measured it. Classifying every adjacent solo pair by whether round k+1 needed round k's result:

| | cohort | rest |
|---|---|---|
| independent (same tool) | 39.2% | 27.8% |
| independent (mixed tools) | 41.2% | 46.5% |
| **dependent — cannot batch** | **19.6%** | 25.7% |
| **batchable** | **80.4%** | 74.3% |

**80.4% of merge candidates on the cohort are independent, and batching is worth more there (22.8%) than elsewhere (13.6%).**
The prize and the feasibility improve together, which is unusual and is why I'd rank this first.

Two independent derivations agree: the batching model implies $0.0753 per round removed; the marginal-round calculation gives $0.0762. Agreement to ~1%.

**Caveat, stated plainly:** the independent bucket is Grep→Grep 44 / Read→Read 25 / Bash→Bash 9. Read→Read is solid (most target a different file). Grep→Grep is the soft half — a new regex inspired by the previous result would not literally appear in it, so some are misclassified as independent. **Treat 80.4% as an upper bound.**
Also: an agent told to batch may over-fetch, which would eat some of the gain. Worth an A/B rather than assuming.

**2. Thinking-strip — 7.9% of cohort spend.**
`STRIP_PRIOR_THINKING` is **off for every reviewer session**, verified three ways: `/_strip` reports `effective: 0, l2: false`; no capture carries a `strip_thinking` transform record; and thinking blocks are present on the wire at 0.77–0.94 per assistant message (captures are post-transform, so that is ground truth).
Worth **7.9% on the cohort** (median 6.7%, max 13.2%).

This is below the 16–22% seen historically elsewhere, because reviewers think in short bursts (median 560 thinking tokens/round) against very large tool_result payloads.
Consumer opt-in per session: `[wirescope:strip-thinking on]` or `POST /_strip?session=&on=1`.

The two levers are near-independent: batching removes rounds, the strip shrinks what each surviving round carries. **Combined ceiling ≈ 30% of cohort spend.**

## What I'd suggest exploring, in order

1. **Fix the denied-Bash waste.** 8 wasted round trips across 5 sessions. Unambiguous, no measurement needed.
2. **Turn on thinking-strip for reviewer sessions.** ~7.9%, mechanical, already built, no behaviour change.
3. **Test whether the reviewer template can encourage parallel tool calls.** Biggest prize (22.8%) and needs no prediction. A/B it — the risk is over-fetching, which is measurable.
4. **Don't pursue preloading.** All three candidates tested under the corrected rule fail or are
   marginal (see above): plan-doc dead on hit rate, pointer pack dead on relevance, caller index
   worth ~1–2% and disputed. The weight bar (~1k tok) is real but turned out not to be the binding
   constraint — **relevance is**. Bogdan's original instinct that this might simply not be
   optimizable is where the evidence lands for the preload half.
5. **Investigate the no-diff population.** 42% of sessions, 44.7% of spend, more expensive than
   diff reviews (22.0 rounds vs 13.9), and none of the diff-derived bootstrap ideas apply. Their
   first read is stereotyped enough to *point at* (`journal.md` 6/25) but not to *preload* (24%
   hit rate vs 45% break-even) — which is precisely the pointer-vs-content distinction above.

An honest bottom line: **the loop is mostly not mechanical.** 72% of calls chase seams into unchanged code, which is the actual review work rather than overhead. The reviewer is expensive because reviewing needs a lot of context, not because it wastes many requests. The wins that exist are in *how* the calls are issued and *what rides along with them*, more than in doing fewer of them.

## Reproducing this

Four scripts in `proxy-lab`: `analyze_reviewer.py` (corpus walk + lever pricing, ~10 min), `reviewer_stats.py` (distributions), `reviewer_output.py` (output anatomy), `reviewer_batchable.py` (dependency classification).
Captures: `~/Library/Application Support/clodex/wirescope/logs`, per-session dirs. Prices are opus-5 (read 0.5e-6, write 6.25e-6, output 25e-6 USD/token).

Known limits: prices assume 2.98 chars/token for message JSON (measured); thinking signatures are base64 and probably tokenize worse, making the 7.9% strip figure a floor. The cohort is n=21 and the git-bash shape is n=2 — no shape-level claim is safe there. `_no-session` captures (4,768 unattributable reviewer requests) are excluded, so this is the attributable subset, not total reviewer spend.
