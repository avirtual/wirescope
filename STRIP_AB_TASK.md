# Strip A/B — shared task protocol

You are one of two parallel sessions running an informal cost A/B for the
wirescope proxy. One session has prior-thinking strip ON (L2), the other OFF
(L0). The point of this file is to make both sessions generate roughly the
same *kind* and *amount* of work — reasoning-heavy, accumulating turns — so the
operator can compare how cost evolves between the two. It is not a controlled
experiment, just a matched workload.

## Rules (follow exactly)

1. **Do exactly ONE step per response, then STOP and wait.** Do not batch steps
   and do NOT self-continue with tools. Generating ~12 separate *user* turns is
   the whole point: the proxy's thinking-strip only fires when a genuine user
   text message advances the turn boundary (a tool-loop stays "current turn" and
   is never stripped), so each step must be its own real turn. Any incoming
   message — "next", "go", anything — advances you to the next step. The trigger
   content is irrelevant; just do the next step and stop. (The operator may
   automate these triggers; do not try to drive yourself.)
2. **Think hard on every step.** Reason the problem through genuinely before
   you answer — extended, substantive reasoning. The strip acts on thinking, so
   thin/no-thinking turns produce nothing to measure. Treat each step as a real
   design problem, not a summary.
3. **Each step must reconcile ALL prior constraints.** Before answering, re-engage
   everything decided in earlier steps and show how the new requirement fits or
   forces a revision. This is what makes the context (and the thinking corpus)
   accumulate — the condition the strip is designed for.
4. **No tools.** Pure reasoning + prose answers. Do not Read/Write/Bash/search.
   Tool traffic muddies the read/write cost split we're trying to observe.
5. Keep each answer **substantial but bounded** — aim for a few hundred words of
   actual design reasoning per step, not a treatise. Consistency between turns
   matters more than length.
6. At the **end** (after the last step), do nothing special — the operator
   pulls `/_report?session=<id>` for each session and compares.

## The task: design a distributed job scheduler, incrementally

Build the design up one constraint at a time. Each step ADDS a requirement and
must keep every prior requirement satisfied.

- **Step 1 — Core.** Design the core of a distributed job scheduler: the job
  model, how jobs are stored, how a pool of workers claims and executes jobs
  exactly-once. Reason through the central data structure and the claim protocol.
- **Step 2 — Scheduling.** Add scheduled/recurring jobs (cron-style) on top of
  the Step-1 core. Reason through how time-based dispatch coexists with the
  claim protocol without double-firing.
- **Step 3 — Retries & failure.** Add retry-with-backoff and dead-lettering for
  failed jobs, keeping exactly-once-success semantics intact.
- **Step 4 — Priorities & fairness.** Add per-tenant priority and fairness so no
  tenant starves another, without breaking ordering guarantees from prior steps.
- **Step 5 — Multi-region.** Make it span regions. Reason through replication,
  clock skew, and which guarantees weaken under partition (be honest about CAP
  trade-offs against everything decided so far).
- **Step 6 — Dependencies (DAGs).** Support jobs that depend on other jobs
  (DAG execution), reconciled with retries, priority, and multi-region.
- **Step 7 — Backpressure.** Add backpressure and graceful degradation when the
  system is overloaded, keeping fairness and dependency guarantees.
- **Step 8 — Exactly-once side effects.** Tighten "exactly-once" to cover
  external side effects (idempotency keys, the dual-write problem), reconciled
  with retries and multi-region.
- **Step 9 — Observability & audit.** Add an audit log / replay capability that
  can reconstruct any job's full history, without violating the data model.
- **Step 10 — Cost accounting.** Add per-tenant resource/cost accounting, and
  reason through where it sits relative to fairness and backpressure.
- **Step 11 — Security & isolation.** Add tenant isolation and a threat model;
  reconcile with multi-region and the audit log.
- **Step 12 — Final synthesis.** Produce a coherent final architecture and argue,
  constraint by constraint, that it satisfies EVERY requirement from Steps 1–11.
  Call out any remaining tension you could not fully resolve.

If the operator wants more turns, continue past Step 12 by stress-testing the
final design against new failure scenarios, one per turn.

## ⚠️ VERIFY THE BOUNDARY AFTER STEP 1 (do this before trusting the run)

The driver may be an Anthropic **task / Stop-hook** that re-prompts you ("have
you finished your task?") at each turn's end, instead of a manual operator
message. That is the intended hands-off mode — BUT it only produces valid data
if the hook's reinjection lands on the wire as a **real user turn** (role=user
with a text block). The thinking-strip (the thing being measured) ONLY fires
when a genuine user text message advances the turn boundary; a tool_result or a
`<system-reminder>`-shaped nag stays "current turn" and the strip never fires —
in which case the L2 session would silently measure NOTHING.

**So: after Step 1 + the first hook/trigger fire, the L2 session must confirm
the boundary advanced before continuing the whole run.** Check either:

- `curl -s "localhost:7800/_context?session=<id>"` → main agent `composition`:
  the `thinking` token line should DROP on the L2 session after a hook-driven
  step (prior thinking stripped). If it keeps climbing identically to L0, the
  strip is NOT firing.
- Or read the latest capture `logs_main/<session>/<NNN>-*.request.json` and look
  at the LAST message: is the nag `role:"user"` with a `text` block (→ real
  turn, strip fires, GOOD) or a `tool_result` / system-reminder (→ no boundary
  advance, BAD)?

**If it fires:** proceed with all 12 steps — and record the wire fact
("Stop-hook reinjection registers as a real user turn, advances the strip
boundary") in the changelog; it's genuinely useful.

**If it does NOT fire:** stop and tell the operator — fall back to an automated
external `"next"` sender (a timed loop firing a plain text message into each
session), which is guaranteed to advance the boundary. Do not run all 12 turns
on a non-firing setup; it wastes the run.
