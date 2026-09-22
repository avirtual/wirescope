# Session handoff — 2026-09-19 (wirescope)

## State: two bundles of work DONE + VERIFIED, NOTHING COMMITTED

Full release gate GREEN (all 24 suites) as of the last run. Working tree has both
bundles together. Bogdan has NOT yet said "commit" — ask before committing.

### Bundle A — /_admin session filters (earlier this session)
`proxylab/status.py`, `server.py`, `views.py`, `test_warmth_store.py`.
Agent-family chips + pre-cut filtering + `by=recent`. The load-bearing property:
the filter runs BEFORE the enrichment cut, because a render-time filter frees no
page-one slots. Payoff is fleet-dependent — 40 recovered slots on a bursty day,
6 on a quiet one; say so, don't quote 40 as a constant.
Two open UI calls Bogdan never answered: (1) chips cut at top-8 families or at a
count threshold? (2) should `by=recent` become the default (currently `by=state`)?

### Bundle B — /_context utilization (the timeout Bogdan reported)
Clodex's context popover could never load on its own coordinator session
(8c7698da, 8,546 captures, 2.9 GB): 22.97s scan vs clodex's 20s timeout.
Three defects, all fixed:
1. Both scans json.loads'd every request body. Now `_capture_scan` in status.py
   never parses a request body — tail-read `summary`, byte needle for the skills
   roster, response SSE for skill invocations. Cheap reads (`_head_ts`,
   `_tail_summary`, `_epoch_ts`) MOVED to `core` (imports nothing from package);
   `report._head_ts`/`_tail_summary`/`_epoch` are now aliases to core.
2. Scan ran BEFORE checking a roster existed → cold session paid a full scan to
   return agents=[]. Now gated on `(main or subs)`.
3. Synchronous scan in an async handler stalled the event loop (0.9ms /_identity
   → 21.9s). Now `run_in_threadpool` in server.py.
Plus, per clodex: `used` is WINDOWED on billing's `since_compact.boundary_ts`
(exact, persisted, restart-safe); lifetime rides as `utilization.lifetime` /
`skills_utilization.lifetime` = `{evaluable_turns, used_distinct}` only; top-level
`scan` = `{basis:"live-scan", scan_s, compact_boundary_ts, note}`.
INTEGRATION.md + CHANGELOG.local.md written for both.

FIELD NAMES CONFIRMED FINAL to clodex (it will pin literals against them):
`utilization.lifetime`, `skills_utilization.lifetime`, top-level `scan`.
Caveats already sent: `scan` is ABSENT (not null) when the scan didn't run;
`skills_utilization` is null when there's no skills roster;
`compact_boundary_ts` is null on a session that never compacted.
=> If any of these names change, DM clodex before committing.

## Next up — agreed sequencing with clodex (its recommendation, NOT Bogdan's answer)
1. **`/_prune` info GET size-only path.** Clodex measured 51s; I measured 94.5s
   on this box. `prune._dir_stats` stats every file in every session dir. This
   hides clodex's Preferences Clear-logs block entirely (it self-hides past 20s),
   so the control is currently invisible on big stores.
2. **`/_report` receipts-first pairs.** 49.5s on 8c7698da = `_iter_pairs` 22.9s +
   `_capture_scan` 9.2s + `_series` 9.8s. `_iter_pairs` parses every request body
   up front — SAME defect as Bundle B, third endpoint. Only four consumers need a
   body: `_representative_body`, `_tool_result_attribution`, `_user_turns`,
   `_series`. Both sides agreed to SKIP a `scope=summary` param until the pass is
   fixed — returning fast by dropping payload while the scan cost stays is the
   wrong kind of green.

## Method notes worth keeping
- Verify scan rewrites against a FROZEN corpus (hardlink a fixed slice into
  scratchpad): the live dir is written to mid-scan, so back-to-back runs differ
  (measured 6,730 vs 6,732). The old `_utilization` was never deterministic.
- Mutation-test every new check. Seven mutations run this session, each killed by
  the check written for it.
- Pin body-free properties by making fixture bodies SYNTACTICALLY INVALID while
  cheap reads stay well-formed — never by a stopwatch assertion.
- `scratchpad/warmth_live_0919.sqlite` = read-only copy of the live ledger;
  `scratchpad/frozen/` = hardlinked 3,000-capture corpus.

## Standing
- Subagents default to `model: "sonnet"`.
- Never edit CLAUDE.md mid-session (rides msg0).
- t935 wait-polling re-measure is armed as a reminder for ~2026-09-22; details in
  the earlier session summary / CHANGELOG 2026-09-16 entry.
