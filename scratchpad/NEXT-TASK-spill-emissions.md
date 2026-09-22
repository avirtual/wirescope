# NEXT TASK (2026-09-21): count MODEL-EMITTED pointer tokens — regression hunt

Bogdan: "check the sessions from the past few hours to count how many times the model
itself emitted the word [pointer token] — we have some regression there."

The token is the at-sign form: `@` immediately followed by `spill`, usually `@spill:<16 hex>`.

## What is being counted

Clodex's intent transport rewrites a long intent BODY into a short pointer of that form and
writes the body to `~/.clodex/spill/<agent>/<hash>.md`. That rewrite is the HARNESS's job.
The regression = the MODEL emitting that token itself, in its own output text, instead of
writing the body and letting the harness make the pointer.

**I did this myself repeatedly in the session being cleared** (seat `wirescope`, session
1a6a28f7-3e86-4580-8988-a12509603bac / capture dirs under the log root below). Twice the DM
failed with "could not be read (missing)" because the pointer I emitted named a file nobody
had written. So this seat is one of the positives — do not be surprised, and do not exclude it.

## Where to look

Capture root: `~/Library/Application Support/clodex/wirescope/logs/<session-uuid>/`
Per request: `<seq>-<agent>-<role>-<model>-<hhmmss>.request.json` and `.response.json`.
The model's OWN output text is `meta.text` in the .response.json (schema confirmed: top level
`seq/agent/role/model/session_id/status_code`, then `billing.tokens.*`, `meta.text`,
`meta.message_id`, `meta.stop_reason`, `meta.tool_uses`, `meta.content_block_types`).
If `meta.text` is empty on a 200, fall back to the SSE capture for that stem.
Filter `status_code == 200`. Window: last ~8h (Bogdan said "past few hours"; 2026-09-20 evening
through 2026-09-21 early morning).

## THREE TRAPS — this is why the count is not a one-line grep

1. **The apparatus matching itself** (CLAUDE.md, the 611-hits/0-firings lesson). This repo
   CONTAINS `SPILL.md` and the wrapper prose documents the token. A grep over request bodies
   matches agents READING the docs/source, and matches the system prompt itself. Count ONLY
   assistant output (`meta.text`), never request bodies, never tool_results.

2. **Re-shipped history multiplies the count.** Once emitted, the token rides in `messages[]`
   on every later request of that session. Counting occurrences across requests inflates by
   the number of remaining turns. Dedupe by `meta.message_id` (one response = one emission
   event) and count per response, not per request.

3. **The harness's own rendered pointer looks identical.** A pointer the harness generated and
   one the model typed are the same bytes. Distinguishing signal: a model-emitted one is
   usually INSIDE the visible reply text / mid-prose, and the strongest evidence is a pointer
   whose target file DOES NOT EXIST in `~/.clodex/spill/<agent>/` — that is a provable
   fabrication. Cross-check every hit against that directory; report `resolvable` vs
   `dangling` separately. Dangling = certain regression. Resolvable = needs the placement
   check before claiming it.

## Deliverable

Count by agent and by hour, with: total emission events (deduped), dangling vs resolvable,
first/last timestamp, and 2-3 verbatim excerpts. State the n and the window explicitly.
A zero for some agent is only meaningful beside a positive control (this seat should be > 0 —
if the detector returns 0 for `wirescope` seats tonight, the DETECTOR is broken, not the fleet).

## Context that must not be re-derived (concluded, do not redo)

Strip-level work for opus-5 vs fable-5-1 is FINISHED. Final verdict, receipt-derived:
- Mid-turn thinking strip + marker gate: **ON-OFF = in*U - T*(w + r*k)**, where U is the
  MEASURED `input_tokens` of the next round. Net **-4.2% of carriage on opus, -1.1% on fable**
  (26,531 / 1,484 thinking rounds, 14d). It is a WIN on both; keep it on.
- The tail is NOT rewritten: write after a thinking round 1,409 tok vs 1,449 after a clean one
  (opus). Cost is exactly ONE extra uncached read; the `w*S` and `r*S*k` terms cancel.
- Fable's payoff is ~91% "thinking never written" (w = 80x its read); opus splits write/re-read.
- Break-even thinking/round: ~448 tok opus, ~815 fable (actual means 689 / 1,107).
- Turn length: opus mean 7.61 req/turn vs fable 3.38 — fable does NOT run longer turns.
- I withdrew an earlier `STRIP_MIDTURN_THINKING=0` recommendation and told clodex to REVERT
  t1045. Root cause of that error: I inferred the uncached span from prefix growth
  (W[i+1]-W[i]-think) instead of reading `input_tokens`; that overcounts ~3.7x.
- Off/L1/L2 in clodex's UI == proxy levels 0/1/2; `on` is already a hard alias for L1
  (WIRESCOPE.md:174, transforms.py:2159). No new level is needed.
- Thin evidence, stated as such: fable 150+ req seats are n=2 (+7.0%).

Also answered tonight (durable copy at `scratchpad/APPEND-PROMPT-STALE-2026-09-21.md`):
clodex seat `clodex` was served a system block missing three grammar rows; the wire is
byte-exactly the PREVIOUS revision of append-prompt.md (wire segment 56,684 B + 3 rows
1,697 B = 58,381 B = the file), and the 23:45:28 respawn did NOT pick the edit up.
