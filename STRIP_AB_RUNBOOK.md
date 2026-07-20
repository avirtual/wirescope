# Strip A/B — loop-driven runbook

This is the hands-off driver for `STRIP_AB_TASK.md`. Read that file once for the
full rules and the 12-step task; this file is only the *operating procedure* so a
timed loop can advance you instead of the operator typing "go" 24 times.

## How the loop works

- The operator (or you) arms `/loop 90s next` once. Every ~90s of idle, the loop
  injects a plain user-text message `next` into the session.
- A `/loop` fire lands as a **real `role:user` text turn** — that is exactly the
  boundary advance the thinking-strip needs to fire. (A Stop-hook nag or a
  tool_result does NOT; see the boundary check below.)
- The trigger word is irrelevant ("next", "go", anything). On every incoming
  message you do **the next undone step and STOP**.

## Your per-turn procedure

1. Look back over your own prior responses in this session and find the highest
   step number you have already completed (0 if none).
2. Do **exactly the next step** (that number + 1) from `STRIP_AB_TASK.md`.
3. Obey the task rules: **think hard**, **reconcile ALL prior constraints**,
   **no tools**, a few hundred words of real design reasoning, then **STOP**.
   Do not batch steps. Do not self-continue. Do not Read/Write/Bash/search.
4. Begin each answer with a one-line header `=== STEP N ===` so the step counter
   stays unambiguous across turns.

## Step ledger (the task, condensed — full text in STRIP_AB_TASK.md)

| N | Step | Adds |
|---|------|------|
| 1 | Core | job model, storage, exactly-once claim protocol |
| 2 | Scheduling | cron/recurring without double-fire |
| 3 | Retries & failure | backoff + dead-letter, keep exactly-once-success |
| 4 | Priorities & fairness | per-tenant priority, no starvation |
| 5 | Multi-region | replication, clock skew, honest CAP trade-offs |
| 6 | Dependencies (DAGs) | job-on-job deps reconciled w/ retries+priority+region |
| 7 | Backpressure | graceful degradation under overload |
| 8 | Exactly-once side effects | idempotency keys, dual-write problem |
| 9 | Observability & audit | replayable full job history |
| 10 | Cost accounting | per-tenant resource/cost vs fairness+backpressure |
| 11 | Security & isolation | tenant isolation + threat model |
| 12 | Final synthesis | prove every constraint 1–11 holds; name residual tension |
| 13+ | Stress tests | one new failure scenario per turn, indefinitely |

## ⚠️ MANDATORY boundary check — after Step 1, before trusting the run

The strip only measures anything if the trigger advances the turn boundary as a
real user turn. After Step 1 + the first loop fire, confirm it (this proxy is not
in your own request path, so you can curl it):

- `curl -s "localhost:7800/_context?session=<your-id>"` → main `composition`:
  on the **L2 (strip-ON)** session the `thinking` token line should DROP after a
  loop-driven step. If it climbs identically to L0, the strip is NOT firing.
- Or read the latest `logs_main/<session>/<NNN>-*.request.json`; the LAST message
  must be `role:"user"` with a `text` block (good), not a `tool_result` /
  `<system-reminder>` (bad — no boundary advance).

**Fires →** proceed through all 12 steps; record in the changelog that a loop
(real user-text) fire advances the strip boundary.
**Does NOT fire →** stop and tell the operator; do not burn the run.

## Arming

After you finish Step 1 and pass the boundary check, run:

```
/loop 90s next
```

To stop: cancel the loop (`/loop` off / the cancel command) — or just stop
responding to `next` once Step 12 (or the agreed stress-test count) is done.
