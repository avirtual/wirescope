# /_report receipts-first — measured design notes (2026-09-19, wirescope)

Measured on clodex's coordinator session `8c7698da-edcd-4350-b79b-58820c0c6fa5`
(8,761 request captures, 3.11 GB of request bodies), LOG_DIR = the live clodex store.

## The budget (measured, not estimated)

| path | cost |
|---|---|
| `session_report(detail=0)` | **32.33s** |
| `session_report(detail=1)` | **50.40s** |
| `_iter_pairs` alone | **17.04s** |
| ├─ full `json.loads` of every `.request.json` | 10.07s |
| └─ `.response.json` + `.warmth.json` parse | 6.01s |
| cheap body-free reads (`_tail_summary` + `_head_ts`) over all 8,761 | **6.73s**, 8761/8761 resolved |

Both entrypoints are past clodex's 20s timeout, so the report has never rendered
on a session this size.

**The ceiling is lower here than it was for `/_context`.** That fold went 7.35s ->
0.045s because it could skip files entirely. Here the ~6s receipt parse is a FLOOR:
every pair needs its `.response.json` (billing) and `.warmth.json` regardless of
what we do with bodies. So the honest target is roughly:

    17.04s -> ~7s for _iter_pairs (drop the 10.07s body parse, keep the 6.01s receipts)

and the rest of the 32s must come from the four body consumers below. Do NOT
promise a 362x here; the shape of the problem is different, and saying so early is
cheaper than walking it back.

## The four body consumers (only these need a request body)

1. `_representative_body(pairs, line)` (report.py ~line 352) — wants the LAST
   tool-loaded body on a line. Needs **ONE** body, found by scanning backwards.
2. `_user_turns(pairs)` (~line 772) — wants the LAST main body ("the full
   conversation lives in the last/largest main request body"). Needs **ONE**.
3. `_tool_result_attribution(pairs)` (~line 455) — iterates messages of EVERY
   pair. The expensive one; see below.
4. `_series` (~line 949, `detail=1` only) — `status._composition(body)` per
   request. Inherently per-request, so this is what keeps `detail=1` costly.

(1) and (2) are nearly free to fix: load one body lazily instead of all 8,761.

## The real problem in (3), and the fix that already worked once

`_tool_result_attribution` walks every message of every request body and dedupes
tool_use ids with `seen_use`. That dedup set is the tell: **history re-ships every
prior tool_use on every turn**, so this scan is quadratic in content — it reads the
same tool_use thousands of times and discards all but the first.

This is EXACTLY the defect fixed in v0.6.68 for the skills tally: a turn's NEW
tool_use blocks debut exactly once in that turn's own **response SSE**, so reading
the SSE counts each call once by construction rather than by bookkeeping. See
`status._sse_skill_names` / the v0.6.68 changelog entry for the precedent.

CAVEAT that must be checked before relying on it: `tool_result` blocks (the
`result_tokens` half) appear only in the NEXT REQUEST's history, never in a
response. So the SSE trick covers the `calls` / `read_targets` / `cheaper` half
cleanly; the result-token attribution needs either (a) the next request's body, or
(b) a bounded tail read. Verify against a frozen corpus before assuming.

## Method rules that apply here (earned earlier this session)

- Verify against a FROZEN hardlinked corpus, never the live dir — it grows
  mid-run and produces false inequality. `other_bytes`-style drift is the tell.
- Mutation-test every new check. Two mutations survived this session and BOTH
  were real gaps (a vacuous assertion, an untested precedence).
- Capture stems are NOT zero-padded (`9998-` > `20322-`): never key anything on
  lexical order.
- A model difference between adjacent CAPTURES is not a model swap — subagents
  and side-calls share one session_id. Filter to main-line before concluding.

## Agreed with clodex

- SKIP a `scope=summary` param until the pass itself is fixed: returning fast by
  dropping payload while the scan cost stays is the wrong kind of green.
- `/_report` is next; clodex confirmed "go ahead".
