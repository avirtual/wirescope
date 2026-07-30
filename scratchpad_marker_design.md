# Owned mobile marker — design (2026-07-21, Bogdan)

## The problem with today's reactive gate
The mobile message-tier marker's position is a per-request READ of where the CLI
put its rolling marker. When the CLI omits it (seen live, ~5/46 turns arm-a), the
read falls back up to the msg0 bundle — sometimes near, sometimes 18k deep. "Just
an accident, not a strategy" (Bogdan).

## The strategy
Wirescope OWNS the mobile marker. We stop reading the CLI's marker position. Each
request:
1. Compute the deepest SAFE frontier ourselves (halt = deepest consumed block that
   survives our own strips = lowest_live - 1; = last message on a clean tail).
2. Remember where we placed it, keyed by session_id, PERSISTED in sqlite.
3. Persist the placed anchor + a PREFIX FINGERPRINT by CONTENT IDENTITY (sha1 of
   role+content, ignoring cache_control AND canonicalizing string≡singleton-text-
   block — the CLI rewrites tail block→string once settled, so raw representation
   would false-miss).

## Correction after contrarian review (2026-07-21)
Placement is a PURE FUNCTION of the request bytes (frontier = just above the lowest
live block; consumed/live is monotone within a lineage). So:
- Restart re-places at the SAME spot with NO persistence needed, and cache_control
  isn't hashed → a marker move never busts. Restart is correct regardless.
- The "advance-or-hold ratchet" is REMOVED: holding an anchor DEEPER than the fresh
  frontier would cache a block the fresh computation just judged live = unsafe.
  Consumed-monotonicity already gives "never retreat within a lineage" for free.
- Persistence is OBSERVATIONAL, not a placement input. Real value: (a) HONEST
  cold-read accounting — a partial compact can keep the anchored message verbatim
  while rewriting earlier messages, so anchor identity matches but the warm entry is
  cold; the prefix fingerprint classifies lineage same/changed/new; (b) seed for the
  future session-cloning role.
- BUDGET self-managed here (not left to the final clamp): after placing, if >4, this
  transform drops the lowest-value message marker itself (msg0 bundle / stray CLI
  marker below the boundary), NEVER the floor pin or the mobile marker.
Lineage classes: same / changed (compact/edit rewrote prefix) / new (/clear).

## Lineage classification (observational; placement is unaffected)
Compare at the SAME DEPTH: re-find the remembered anchor by sig, fingerprint the
prefix up to IT (not up to the new frontier — else a legit frontier advance grows
the prefix and false-alarms every turn, contrarian #2).
- no persisted row (`/clear` rotated the id) → `lineage:new`.
- remembered anchor absent (full compact summarized it away / divergence) →
  `lineage:anchor_gone`.
- anchor present but prefix ≤ anchor differs (partial compact rewrote earlier
  context) → `lineage:prefix_changed` = warm entry did NOT survive.
- anchor present + prefix intact → `lineage:same` (+`advanced:true` if the frontier
  moved deeper this turn).
- unmarkable frontier → forget the stale row (no repeat dead lookup).
Placement recomputes fresh from current bytes in ALL cases; lineage is telemetry.

## Budget self-management (contrarian #4/#5)
After placing, if >4 markers, drop the lowest-value MESSAGE marker below the pin
boundary ourselves (msg0 bundle / stray CLI marker) — never pin (@boundary) or
mobile (@frontier). If STILL >4 (unexpected marker between boundary and frontier),
log `budget_overflow` loudly rather than let the final clamp silently drop the pin.

## Schema migration
`marker_state` gains `pfp` via `ALTER TABLE ... ADD COLUMN pfp TEXT` in
register_schema (store._apply swallows the OperationalError on already-present) —
old DBs migrate in place, no manual reset.

## The two message-tier markers (budget = 4: 2 sys + these 2)
- FLOOR = pin@settled boundary (existing `_pin_settled_breakpoint`, monotonic).
- PROBE = the owned mobile marker (this change), at the frontier.
- msg0 bundle = DEMOTED to backup: no longer claims a default slot; the existing
  `_enforce_marker_budget` drops it (earliest non-tail message marker) when the two
  owned markers + 2 sys fill the budget. Kept in code (env_relocate still plants it)
  for the future session-cloning role Bogdan flagged.

## Why donor-migration disappears
The whole reason `_plant_fallback_marker` migrated msg0 was budget contention
(msg0 + pin + rolling-tail = 3 wanting 2 slots). With the probe owned and msg0
demoted-via-budget-enforcer, contention is gone — no manual migration, no mode zoo
(relocated/dropped/tail_fallback/clean_tail_fallback all collapse into: strip CLI
message markers above the boundary, place probe at frontier).

## Flag
`OWN_MOBILE_MARKER` default OFF. Current reactive gate stays default so the A/B has
a clean control. New strategy is the experimental arm. A/B tomorrow tests the two
strategies (not the reactive patch vs .37).
