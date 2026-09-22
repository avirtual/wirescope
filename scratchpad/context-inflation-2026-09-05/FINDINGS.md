# Context inflation on clodex hand seats — 2026-09-05

Bogdan: "new hand agents get really high context on doing tickets (hand-654) …
if 200k becomes 300k it doesn't truly matter what the cause is, it is objectively worse."

Corpus: live clodex capture dir (`~/Library/Application Support/clodex/wirescope/logs`,
52 GB, 2,662 sessions). Unit = each hand/review seat's LAST request (the accumulated
window). Window = `input + cache_read + cache_creation` off the receipt.
Scripts in this directory; run with `LOG_DIR=<live dir>`.

## THE HEADLINE — the inflation is real and it is NOT the bash instruction

Mean window per hand seat, by week:

```
                    08-10      08-17      08-24      08-31     delta
TOTAL              86,043    101,453     87,407    107,960   +21,917  (+25%)
seats/week            218        158        233        207
```

Decomposition (Oaxaca; `oaxaca.py`):

```
VOLUME effect (more ops)   :  +9,100 tok/seat   68%
PRICE effect (cost per op) :  +2,706 tok/seat   20%
interaction                :  +1,602 tok/seat   12%
```

**Two-thirds of the rise is simply more operations per ticket** (40.6 → 51.5 ops/seat).
No transform touches that; it is a scope/efficiency question, not a carriage one.

## WHY "cheaper reads + cheaper writes" DID NOT make it cheaper

The substitution never happened for reads — bash reads were ADDED, not swapped in:

```
reads      native 10.1 -> 10.2 (+0.1)   bash 4.0 -> 7.5 (+3.5)   TOTAL 14.1 -> 17.8
writes     native  5.0 ->  2.0 (-3.0)   bash 1.9 -> 7.4 (+5.5)   TOTAL  6.8 ->  9.4
searches   native  9.3 -> 11.4 (+2.1)   bash 7.2 -> 8.5 (+1.3)   TOTAL 16.6 -> 19.9
```

Counterfactual (week-4 volume at week-1 route mix): routing SAVED ~3,758 tok/seat on
reads, COST ~993 on writes, ~0 on searches. Net ≈ +2,700 saved — swamped 3.4:1 by the
+9,100 volume effect. **The routing change was mildly good and completely irrelevant
to the total.**

## PER-OPERATION PRICES (`perop.py`, paired tool_use+tool_result)

```
operation                     n    inTok   resTok   TOTAL/op
Read                       8464       46    2,659      2,705
bash:read (cat/sed -n)     3903       82    1,353      1,435   0.53x  bash WINS
Grep                       7679       67      883        950
bash:search                4827      127      416        543   0.57x  bash WINS
Edit                       1861      516       30        546
bash:write/edit(heredoc)   3835      736      181        917   1.68x  per CALL
```

**CORRECTION — the 1.68x is wrong as a comparison.** A heredoc averages **1.50 edit
operations per call** (44% carry 2+ replaces); native Edit does exactly one. Per
EDIT OPERATION: native 659 vs heredoc **571 = 0.87x, heredoc is CHEAPER**. Modelled
over all 5,885 ops, converting to native Edit would cost **+518k tok**. Do NOT steer
edits back to Edit. (`edits3.py`)

Native Edit also hides the **read-before-edit gate**: 1,566 tok per file unlocked
(174 of 649 first-edits show the gate Read in-window; rest read earlier/created
fresh, so it is a lower bound). Heredocs pay zero. (`gate.py`)

## WHY bash READS ARE CHEAPER — bounding discipline, not magic

```
route                       n      p50      p90       max    totalTok
Read (offset/limit)      6607    1,333    3,454    28,493  11,529,726
Read (no limit=2000)     1766    3,059   14,502    49,558   9,720,784
bash bounded             8231      482    1,832     8,478   6,279,117
bash full cat            1655       24    2,053    10,048     936,342
```

`Read` with no `limit` defaults to **2000 lines** — 1,766 such calls carry 9.7M tok
alone. bash does MORE fetches (9,886 vs 8,373) for a THIRD of the bytes.
Bounded share is near-identical (Read 79%, bash 83%), so the gap is what an
unbounded call costs on each route, not how often it happens.

**Not randomized** — agents may pick `sed -n` for files they know. Treat 3x as an
upper bound on the true effect.

## REPEAT FETCHES (Bogdan's hypothesis: cat has no readFileState)

Directionally the opposite of the hypothesis:

```
route     fetches  distinct  fetch/file
Read          496       227        2.19
bash          265       203        1.31
```

But **100% of repeat Reads carry offset/limit** = paging, not re-reading. Line-range
union vs sum: actual overlap **5.1%** (571/1,277 path-pairs have any). Only 5% of
repeat Reads precede an Edit, so not the gate either. (`repeat2.py`, `overlap.py`)

**Wrong instrument warning:** an earlier pass hashed tool_result BODIES and got 0.0%
redundancy — a re-read after an edit differs by bytes and scores unique. **Key on
PATH, not on content hash.**

## WHERE THE RECLAIMABLE WEIGHT IS

**Supersession — 45% of all edit payload is dead** and it COMPOUNDS with ticket length:

```
window bin       n   medEdits  medSupTok  medSup%win
0k-60k          26          4        924        1.7%
120k-180k       65         25      9,429        6.4%
180k-240k       19         36     18,889        9.2%
240k+            9         52     23,007        8.6%
```

An edit to `team-tickets.js` is dead once a later edit lands, but both re-ship every
request until the ticket ends. Hottest: 19 edits to one file (t634, t472), 16 (t571).
This is CLAUDE.md open item (c), now with numbers. (`waste.py`, `scale.py`)

**Verification echo** — 3,919 bash edits returned 520k tok where native Edit returns
**1 tok**; 19% of results >200 tok. Not amortized by batching.

**Wrapper framing** — 40% of heredoc input is python scaffolding, but batching
amortizes it; already counted in the 0.87x.

## RANKED LEVERS

1. **Native reads → bounded bash reads.** 10.2 Read calls/seat × 2,779 tok = 34,235,
   a THIRD of the window; bash does the same job at 1,239. Seats added bash reads for
   new work and never touched their `Read` habit. ~15k tok/seat. **Prompt change, zero
   cache risk. Biggest lever by far.**
2. **Bash read discipline is DECAYING** — `read:bash` price 981→1,239 (+26%),
   `search:bash` 517→668 (+29%) as adoption spread. Bound it before the routing gain
   erodes. Prompt change.
3. **Supersession stubbing.** Proxy work. MUST ride an existing bust (cold-gated
   sticky latch, like the thinking-strip) — see `transforms.py:1854`
   `STRIP_PRIOR_EDIT_ACKS`: collapsing a prefix block busts downstream, ~$1 on a big
   window to reclaim ~1.4k/turn, breakeven ~1400 turns → **a LOSS if it originates the
   bust** (measured live: busted 174k to save 1.4k). Stub superseded INPUT payload,
   not heredoc output (output is arbitrary, a content bet; native acks are fixed
   boilerplate, which is why those are safe).
4. **Do NOT** steer writes to native Edit. Measurement says backwards.

## t654 specifically

Hottest seat on record: median window 224,152, peak **318,599** (of 307 hand seats;
t645 315,976 and t650 305,898 next — 3 of the top 4 are from the last two weeks).
NOT an efficiency outlier — win/call 1,316 is BELOW the fleet median ~1,440. It is a
VOLUME outlier: 242 tool calls, zero Read/Edit/Write.

```
read (bounded)  58   79,067 tok  35.3%
edit (heredoc)  78   72,549      32.4%     (63,899 of it tool_use INPUT)
search          82   59,345      26.5%
read (full cat)  4    9,897       4.4%
```

Boot load 35,791 tok, decays 35%→12% across quartiles — NOT the problem
(`analyze_prefix.py 3678472b-2567-427a-ad28-c65541a2ae1a`).

## SEPARATE FINDING — the CLI's bash_output_audience_note

"Only you see that command's output — the user's terminal shows at most a few lines…"
Bogdan saw it repeating in clodex-designer-653.

**It is the CLI's own**, baked into 2.1.261 as `bash_output_audience_note`. Gate
`E6n`: fires when stdout is a string, `!ke()` (**interactive only** — headless `-p`
never emits it, verified), and `_k(stdout)` = **more than `A1=3` newlines**. Then
`env.CLAUDE_CODE_BASH_OUTPUT_AUDIENCE_NOTE ?? (jG(o) || Rte(...))` — `jG` =
**unconditionally ON for the fable-5-1 prompt bundle**, every other model needs a
server-side feature gate. Hence 21% of fable-5-1 agents carry it vs 0.04% of opus-5.

Fires **once per qualifying Bash call** and accretes in history (designer-653: 26 Bash
calls → 25 copies → 3,700 ch ≈ 1,241 tok re-shipped every request; some `role:"system"`
messages carry 6 identical copies).

**Cost fleet-wide: 623,239 tok = $0.30.** Economically nothing (0.004% of a $7,488
bill). Same shape as the retired `/_pot` — mechanism real, hypothesis negative.

**The real issue is correctness, not cost:** clodex now has a console tab showing full
Bash output, so the note's premise is FALSE and it can push seats into padding replies
with output the user is already reading. `CLAUDE_CODE_BASH_OUTPUT_AUDIENCE_NOTE` looks
like a kill switch (`??` overrides both gates) but is **UNVERIFIED** — untestable
headless because `ke()` suppresses it in `-p` regardless. Needs a PTY seat.

## METHOD NOTES (cost me time; don't repeat)

- **Aggregator:** a first reconciliation pass used medians and reported **0 for most
  categories** — >half of seats use only one route, so the median of a mostly-zero
  column is zero. **Accounting questions need MEANS**; medians answer "what does a
  typical seat do", never "where did the tokens go".
- **Unit:** per-CALL vs per-OPERATION inverted the edit verdict (1.30x → 0.87x).
  When one side batches, the call is not the unit of work.
- **Confound control:** raw fleet medians showed bash-heavy ≈ mixed (95,210 vs 94,790)
  because they compare a 30-call seat to a 250-call seat. The WITHIN-WEEK control is
  what exonerated the instruction (1,426 vs 1,487, gap ~3%, sign FLIPS in week 4).

---

# ADDENDUM 2026-09-05 (fable): Bogdan was right — the cut date IS the cause. The 09-05 verdict above is RETRACTED.

Bogdan: "it feels like an impossibility that all tickets are more complex and the cut date is exactly when the behavior changed."

## What was wrong with the first analysis

The Oaxaca split treated "ops per seat" as independent of routing. It is not. Under the bash regime the SAME job takes more calls (a bounded `sed -n` fetches ~1/3 of a ranged `Read`; a heredoc edit is followed by a verification `cat`/test run), so the extra calls got booked under VOLUME and the instruction was "exonerated". Per-CALL price comparison was the wrong unit — same mistake family as the per-call edit verdict.

## The cut, dated on the wire

The instruction rides as a trailing `role:"system"` message, one copy per request, byte-identical (md5 823c6003 on 386/386 requests):
"While bypass permissions mode is active: Do your work through the Bash tool wherever it can accomplish the job … rather than using the dedicated Read, Edit, or Write tools. Fall back to a dedicated tool only when Bash genuinely cannot do the job."

Adoption is a STEP, not a ramp: 0/88 hand seats through 08-17 (CLI ≤2.1.233), 5/7 on 08-18 (2.1.234), 100% from 08-19. Model is opus-5 throughout, so it is not a model change.

## Full window reconciliation, hand seats, pre (n=90, ≤2.1.233) vs post (n=211), MEANS scaled to receipt (`recon3.py`)

```
bucket               pre      post    delta      note
out:read:bash     11,312    24,364  +13,051      bash read OUTPUT
out:read:native    9,169       569   -8,600      Read output gone
in:edit:bash       3,302    14,399  +11,097      heredoc INPUT
in:edit:native    10,383     3,213   -7,170      Edit input gone
out:edit:bash      1,159     3,336   +2,177      heredoc echo/verify output
out:search:bash    8,603    11,844   +3,241
asst:text          4,853     7,190   +2,337      more narration (43.7 vs 27.8 text msgs/seat, same 130 tok/msg)
sys:midconv        7,849     9,956   +2,108      clodex SessionStart hook grew 2.4k→4.6k (memory index, roster)
boot:tools         6,603     8,604   +2,002      SendFeedback added (1.9k), SendMessage +560
boot:system        9,957    11,590   +1,634      clodex role prose (Checkpointing / Comments sections)
out:git:bash         818     1,805     +988
WINDOW            92,174   115,820  +23,645
```

Routing-attributable (read+edit+search rows net): **+13.8k of +23.6k = 58%**. clodex-side boot growth (hook + tools + role prose): +5.7k = 24%. Assistant narration: +2.3k = 10%.

## Work-normalized: same files, more ops per file, more window per file (`recon5.py`, corrected op counter)

```
                         pre      post
source files edited/seat  5.4       5.5     ← the WORK did not grow
source edit ops/seat     16.4      22.9     +40%
ops per file             3.03      4.14     +37%
window per source file  22,953   30,560     +33%
```

Retry/verification after a source edit (next 3 tool calls, `retry.py`): bash edits carry a fail-marker in their own result 6.7% of the time (native: 0.0%) and are followed by a test/run 46% of the time (native 35%). Native edits show a HIGHER same-file re-edit rate (49.5% vs 32.4%), so the extra ops are not just retries — a heredoc call batches 1.76 replaces, and the model then re-reads/re-runs around it.

**Bash edits are still cheaper PER OP (524 vs 651 tok, no read-before-edit gate).** What changed is the number of ops per unit of work and the read/verify traffic around each — the regime, not the primitive.

## Reviewer control

520 reviewer seats, 0 carry the instruction (they run without bypass), Read/seat 15.3, bash-read/seat 0.0 — so the FINDINGS "native Read 10.2/seat" lever above was a mixed hand+reviewer number. On hands the Read habit is already gone (0.7/seat post). Lever 1 above is moot for hands.

## Mechanism in the CLI (2.1.261 binary), WIRE-VERIFIED

`$fs()`: fires when `mode==="bypassPermissions"` (clodex spawns every seat with `--dangerously-skip-permissions`) or auto mode; requires Bash + (Edit|Write) in the tool roster AND `nzt()`:
```
nzt() = env.CLAUDE_CODE_THRIFTY_SONIC ?? (forced→true | none→false | cohort→gate tengu_thrifty_sonic)
ba()  = "forced" if fable_5_1_prompt_bundle || tengu_thrifty_sonic statsig || dm(…) — opus-5 arrives here via the server gate
ftr() = env.CLAUDE_CODE_COZY_TEAPOT ?? settings[tengu_cozy_teapot] ?? "strict"   → "strict" | "relaxed"
```
Two texts: STRICT (what hands get) vs RELAXED ("You can do much of your work through Bash when it is the simpler route … prefer Edit or Write when a shell edit would be fragile, such as exact or multi-line replacements, or sed/awk flags that differ between GNU and BSD/macOS.").

Probe through :7800, opus-5, `claude -p --dangerously-skip-permissions`, cwd = empty scratch dir:
```
default                          → STRICT   present
CLAUDE_CODE_THRIFTY_SONIC=0      → ABSENT
CLAUDE_CODE_COZY_TEAPOT=relaxed  → RELAXED
```
(sonnet-5 headless: absent by default — not in the cohort.) Env is read per process; clodex injects per-seat env via the generated `--settings` block (`cli-hooks.js:513`, `settings.env`), so either knob is a one-line change there and needs no CLI pin.

## Recommendation (Bogdan's call; nothing changed)

1. **`CLAUDE_CODE_COZY_TEAPOT=relaxed` on hand seats** first, not THRIFTY_SONIC=0. The relaxed text keeps the genuine wins (bounded `sed -n` reads at 0.53× a `Read`, grep at 0.57×) and steers the fragile multi-line replacements — exactly the ones that fail 6.7% and get re-run — back to Edit. This targets the +37% ops/file directly.
2. Measure it the right way: window per source-file-edited and ops per file, by week, NOT tokens per call. `recon5.py` prints both. If ops/file returns toward 3.0 and window/file toward 23k, keep it; if the read output stays at +13k, add a bounded-read line to the hand template.
3. Separately, the clodex-side +5.7k/seat boot growth (SessionStart hook 2.4k→4.6k; SendFeedback 1.9k on 72% of seats) is a constant per seat and unrelated to routing — cheap to trim, tiny per turn, mention only.
4. Do NOT set THRIFTY_SONIC=0 outright before trying relaxed — the pre-cut regime paid 8.6k/seat in `Read` output that bash reads genuinely halve.

## Method notes (add to the list above)

- **A per-call control cannot exonerate a treatment that changes calls-per-job.** Normalize by a routing-independent work unit (files edited, new text written) BEFORE splitting volume from price.
- **When adoption is a step function on the same date as the effect, the burden of proof is on the null.** The 08-31 within-week control compared treated seats to treated seats (100% adoption by then) and measured nothing.
