# `/_hints` — frozen contract (v1)

The tail-hint control plane. **This surface is called by Clodex main-process code in a shipped app, so it is an API contract against a released binary: a breaking change here means a coordinated update across two products.** Everything below is frozen for v1; additions must be backward-compatible (new optional fields, new native provider names), never renames or semantic changes.

Mechanism and rationale live in `proxylab/hints.py`'s module docstring. This file is the caller's contract.

## Ownership split

**Clodex registers the hints; wirescope owns a content-agnostic slot.** wirescope never authors hint text, never interprets it, and never templates into it — the body is opaque and lands inside a `<system-reminder>` the model treats as authoritative, so there must be no path for anything upstream to compose into it. The one exception is *native* providers, where wirescope measures the fact itself and the consumer only enables it by name.

## What a hint is

A short string injected as a **new trailing text block on the last user message**, `<system-reminder>`-wrapped, on every request that resolves it.

- **Never cached.** The block lands strictly downstream of the deepest `cache_control` marker. Editing a hint therefore costs nothing but its own tail tokens — that is the point of the channel: a control plane you cannot edit without paying for the edit isn't live.
- **Never in the transcript.** Request-side only. This is structural, not empirical: the CLI writes its transcript from its own records, which never contained the addition. Do not attempt to verify it by grepping a transcript — the search can only find copies a session authored itself.
- **One-request lifetime.** A hint rides one request and vanishes; it does not accrete. This is the feature, not a limitation — it is the opposite of PTY injection, which becomes a permanent user message re-billed every turn. Send **volatile state** here; keep **events** on PTY injection.
- **Cost.** Uncached input tokens on every carrying request (no cache discount, by design). ~35 tokens ≈ $0.000175/request at opus rates. **Note "per request", not "per turn"** — see the turn-start gate below.

## The turn-start gate (`turn_start_only`)

**A turn is not one request.** A tool loop is N, and a registered hint rides every one of them at 1× with no cache discount. That is correct for a *constant prohibition*, which has to be present at the moment the model reaches for the forbidden action — typically deep in the loop. It is wrong for anything that answers **"what did the user just type"**: a retrieved memory is relevant on request 1 and is pure carriage by round 14.

So the gate is **per hint**, set on the hint object:

```json
{"hints": [{"id": "memory", "text": "…", "ttl_s": 900, "turn_start_only": true}]}
```

- **Defaults to `false`** — every existing hint keeps riding every request; this is purely additive.
- "Turn start" = the user's message is the newest thing in the window and the model has not yet acted on it. Detected from the **shared settled-turn boundary** (the same one the strips and the pin use, never re-derived), then checking for an `assistant` message after it. Correct on the opus-4-8 shape where a trailing `role:"system"` roster message sits *after* the user turn.
- **Fails open.** If the boundary can't be judged, the hint ships. The gate is a cost optimization with no correctness content, and a hint silently never firing is the worse failure.

  **This is not a general preference for failing open, and the contract deliberately does both.** The placement invariant fails **closed** — it declines rather than risk landing inside a cached segment. The rule is *fail toward whichever side's failure is recoverable*: an ungated hint costs a few tokens you can measure and stop paying, while a hint inside a cached segment is a recurring per-turn bust, and a hint that silently never fires costs the entire thing it existed for. Direction follows the blast radius, not a house style.
- Withheld mid-loop is recorded as `declined: "turn_start_gate"` with the withheld ids — explicitly *not* counted as an unmatched-scope misconfiguration, since it is the gate working.
- Feature-detect via `capabilities.hints.turn_start_gate` on `/_identity`. **Posting the field to a proxy without it is silently accepted and ignored** (unknown keys aren't rejected), which bills per-request — so check the capability rather than assuming.

## Main-line scoping (`main_line_only`)

**The problem it solves: subagents share the parent's `session_id` by construction.** A `?session=` hint is therefore in scope for every `Task`/`Agent` spawn of that session, and there is no finer key on the wire. For a standing prohibition that is merely wasteful. For a **one-shot payload** it is a correctness bug: a subagent's first request consumes a payload meant for the main line, and the main line never sees it.

Measured on 35,405 parent-classified requests in `logs_main`: 2,107 strict cross-role overlaps across 106 sessions (top pairs `parent`/`general-purpose` 478, `parent`/`Plan` 335, `parent`/`subagent` 327). Fan-out concurrency is the normal shape of a session, not an edge case.

**The main line is NOT concurrent with itself**, so a main-line-scoped one-shot lands on one request by construction. Verified at n=33,772 main-line turns: 12 residual overlaps across 10 of 502 sessions, every one a pair with identical message counts where one side's SSE truncates mid-`thinking_delta` with no `message_stop` — abandoned streams the client re-issued. Retries, not parallelism.

### The predicate must be stricter than `role == "parent"`

**This is the trap, and it is not visible from outside.** `writer._classify_role` is a pure function of the inbound request (system-prompt signature + the two wire signals), so it *is* available at injection time — the server already computes it at `server.py:1660` on the same object `inject()` sees, and the ordering is incidental. But it is the wrong predicate here.

`_genuine_subagent` applies a **fingerprint backstop**: when a request's content fingerprint matches the session's recorded main-line fingerprint, it is filed as parent even though a raw subagent signal is present. That under-attribution is *correct for billing* — it keeps a parent turn that leaked a stale agent-id (and a fork-path sub, which clones the parent's first message and so its fingerprint) out of a subagent bucket. It is **wrong for "may this consume a one-shot payload"**, where the same request must be refused.

Measured: **979 requests (2.75% of all parent-classified traffic) carry a live raw subagent signal and are still classified `parent`.** Tested against independent lineage evidence (hash of the first user message, the thing the CLI itself fingerprints on): **676 of the 979 have a lineage DIFFERENT from their session's main line** — genuine subagents the classifier files as parent — and only 303 match the same-lineage fork/leak pattern. An earlier draft of this section attributed all 979 to the fork path; that was wrong, and it mattered less than it should have only because both populations want the same treatment here. Either way the raw-signal predicate refuses them.

> **One field, two consumers, opposite correctness criteria.** The dashboard's row classification and the pop predicate ask different questions of the same signal, and the answer that is right for attribution is wrong for consumption. Reuse the *signals*, not the verdict. Anyone reusing `role` for a third purpose should re-derive which of the two criteria theirs matches rather than assuming one classification serves all.

So the pop predicate reads the raw signals directly and **fails closed** — decline on either, ignoring the fingerprint backstop:

- `writer._billing_is_subagent(obj)` — `cc_is_subagent=true` in the billing header (system block 0), **or**
- a present `x-claude-code-agent-id` request header.

Cost of failing closed: an unserved payload on genuine fork-path main-line turns, bounded by that 2.77%. That is the recoverable side — the consumer re-posts. A mis-served payload is consumed by something that cannot act on it and, worse, is **indistinguishable from "the model ignored it"** at the consumer, so it corrupts the only instrument available for tuning retrieval. Blast radius, per the rule above.

### Independently useful for standing hints

A prohibition registered against a seat currently rides that seat's subagents' requests too. `main_line_only` is the opt-out, and it is the same predicate.

## One-shot payloads — the pop protocol (`once`)

The use case: deliver a large **ephemeral catalogue** (e.g. a memory index) that must reach the model once, influence what it asks for, and then vanish — never entering the transcript, never re-shipping. The model reads the menu, emits `load-memory 2,7`, and the consumer delivers only those bodies through its own channel. The menu itself is never carried again.

```json
POST /_hints?agent=<route>
{"hints": [{"id": "memcat", "text": "…", "once": true,
            "main_line_only": true, "ttl_s": 900,
            "expect_session": "<session-uuid>"}]}
```

### Pop RESERVES at injection and COMMITS on a 200

**A pop must not be consumed by a request that never completes.** Measured across 54,193 captures: **94.08% of forwarded requests reach a 200; 5.9% never do** (3.60% no response at all, 1.19% 404, 0.86% 429, 0.21% 529, 0.04% 400). And failures **cluster** rather than scattering: 426 of 795 sessions are failure-free, but there are 1,160 failure *runs* — median 1, p90 6, **max 38 consecutive**. Popping at injection would silently discard payloads through an entire shed storm.

So the pop is two-phase, using the two points the server already has:

1. **Reserve** — `hints.inject()` stamps the payload onto the request and marks the entry in-flight.
2. **Commit** — `receipts.anthropic` (the single turn-finalize convergence both wires call, which already receives `status_code`, `session_id`, `role`, `agent_header_id`) pops on a 200 and **rolls back otherwise**, leaving the entry armed for the next request.

Rollback is deliberately invisible to the model and visible to the consumer: the entry simply rides the retry. No re-POST needed.

### Keying: agent route, with `expect_session` as the real guard

The key is the **agent route**, because it is nameable in advance. (A subagent-id key was considered and dropped from v1: `x-claude-code-agent-id` is stable across an instance's turns — 594 instances, 84.8% multi-request, median 29 turns — but is *unknowable before that instance's first request*, making it reactive-only, which is the opposite of a menu you want waiting.)

**A route name does not identify a conversation, and this is the load-bearing finding.** Measured on main-line traffic only (`role=parent`, `tools>0`, title side-calls excluded) across 207 routes: **516 route-level session switches between consecutive main-line requests — 117 within 10s, 250 within 60s.** Most are `/clear` rotation, which is sequential and harmless. But **53 are true interleaves**: conversation A ran, B ran, then A *resumed* on the same route — including 10 on a live per-seat Clodex route, tightest resume 27s.

**What the strays are — checked, not assumed.** The first hypothesis was fork-path subagents misfiled as `parent`. **Falsified:** applying the strict predicate (drop every request carrying `cc_is_subagent=true` or `x-claude-code-agent-id`) leaves the count at **53 → 53, zero removed**. Positive control, because an unchanged number is also what a dead filter produces: the same predicate drops 90 of 6,585 parent+tools requests elsewhere, so the filter works and the null is real.

Reading the bodies identified them: **one-shot CLI spawns** — own `session_id`, `cc_entrypoint=sdk-cli`, full 27-tool roster, exactly 2 messages, no subagent signal of any kind, prompt literally `hi`, firing 4–5s apart in the gaps between a long conversation's turns. Ad-hoc probe traffic sharing a live route, not harness machinery (Clodex spawns no one-shot CLI; its hooks only read stdin and write files). On a single route, 41 of 47 sessions were these.

**They are genuinely main lines** — separate top-level CLI processes — so they pass `main_line_only` honestly. That is precisely why the name alone is insufficient: a name-keyed pop would be consumed by a two-message throwaway that exits immediately, the receipt would read *delivered*, and the intended conversation would never see it. **Indistinguishable, at the consumer, from "the model ignored it."**

`expect_session` closes it:

- **Absent** → serve to whatever main line arrives on that route. The **unsafe mode**; correct only for routes known to host exactly one conversation.
- **Present** → serve only if the inbound `session_id` matches; otherwise **leave armed and log a decline**. Silent misdelivery becomes visible non-delivery.

Not mandatory — a required field gets filled with whatever satisfies the validator, and the optional form is still right for standing hints. But **omitting it on a route that hosts spawns is a footgun**, and consumers should post with it always.

> This is available *only* because the namespaces turned out to be identical. The wire `session_id` (parsed from `metadata.user_id`) **is** the CLI's `sessionId`: **758 of 788 distinct wire session_ids (96.2%) exist verbatim as `~/.claude/projects/**/<id>.jsonl` transcript filenames**; the 30 that don't are rotated/swept transcripts and codex v7 ids (codex has no CLI transcript by construction). A consumer reading the id off the transcript symlink can name the conversation it means. There is no mapping layer and no translation race.

The guard generalizes past the case that motivated it: it defends against *any* unenumerated stray on a shared route, which matters because the first attempt to name the threat was wrong.

### The rest of the semantics

- **`ttl_s` is REQUIRED for `once` hints in both scopes** (agent scope otherwise permits `null` = never expires). A stale catalogue describes a decision that already happened — worse than no catalogue. Keep it short.
- **One slot per purpose: use a stable `id`.** Upsert by id means re-posting `memcat` replaces the armed entry, so the newest menu is the one that rides. A fresh uuid per catalogue would leave several armed at once and put two menus on one request.
- **Re-POST after a pop re-arms.** Under `once` a refresh and a re-arm are the same operation, so the merge-upsert ambiguity does not bite here.
- **Posting is out-of-band and never on the request path**, so an unreachable proxy fails loudly in the consumer's own HTTP call and cannot block or delay a turn.
- **State is readable** (`armed`, `armed_at`, `delivered_session`) so *posted-but-never-served* is distinguishable from *never-posted*. `delivered_session` is reported regardless of mode, so even an unguarded pop can be audited after the fact for where it actually landed.
- **`main_line_only` uses the raw-signal predicate** from the section above, not `role == "parent"`.
- **Feature-detect via `capabilities.hints.pop` on `/_identity`, never on `version`.** This is not housekeeping — it is the one failure the consumer cannot see. A proxy without pop support **accepts `once`/`main_line_only`/`expect_session` silently and ignores them** (unknown keys aren't rejected), so the payload never pops and rides **every request for the life of the session**. A catalogue is the largest thing on the wire and it is uncached at 1×, so the ignored-key path is also the most expensive one, and it is invisible from the consumer side: the model receives the menu, behaves plausibly, and nothing anywhere reports an error. Check the capability before posting a one-shot.

## Endpoints

### `POST /_hints?agent=<pattern>`
Constant behavioral hints for a route. **Persisted** (survives proxy restart). `ttl_s` optional.

```json
{"hints": [{"id": "no-noop-bash", "text": "…", "ttl_s": null}], "mode": "merge"}
```

`mode` = `merge` (default, upsert by id) or `replace` (posted set becomes the whole scope).

**`<pattern>` may be a glob** (`fnmatchcase`: `*`, `?`, `[…]`). **Use a glob unless you know the exact wire name.** The wire agent name is not always the name a consumer thinks it registers — Clodex's managed hop rewrites the route to a derived `<team>-<seat>-<hash>` (e.g. `clodex-clodex-889de1bd`) whose hash is unknowable before the first request. `agent=clodex-*` covers a seat family. Exact match takes precedence over a glob on the same hint id.

### `POST /_hints?session=<id>`
Transient **facts** for one session. **In-memory only, never persisted** — a fact that survived a restart would be lying about live state. **`ttl_s` is REQUIRED** and must be `> 0`: a fact with no expiry keeps shipping and reads as current. Expiry is evaluated per request, so a fact lapsing between hops of one turn vanishes on the next hop.

### `POST /_hints?session=<id>&native=<names>`
Enable proxy-measured facts by name (comma/space separated). The proxy owns the text and its freshness; the consumer never writes these bodies (single-sourcing: two writers for one fact means no way to tell which is stale). Unknown names → 400 with an `available` list.

v1 providers, both **silent unless they have something to say** — a provider that said "everything is normal" every turn would train the model to skip the position, disabling the channel for the turn that matters:

| name | fires when | notes |
|---|---|---|
| `upstream_health` | ≥25% shed (429/5xx) over ≥4 requests in 120s | **Process-global by design.** A hint can only ride a request that gets a *response*, so a session at 100% shed cannot receive it — the audience is the observer deciding on a teammate's behalf. Measured on a real storm: global fires, per-route would have fired for nobody. |
| `context_pressure` | window ≥75% full | Reuses the same helper `/_status` reads, so it cannot disagree with the dashboard. |

### Discovery

`GET /_identity` → `capabilities.hints` = `{available, enabled, system_tail_fallback, turn_start_gate, pop, native[], caps{}}`, and `endpoints.hints` = `/_hints`. **Gate on the capability, never on `version`.** Note `endpoints.hint` (no `s`) is an unrelated feature — the per-agent spawner-hint override.

Each per-feature flag exists because that feature's absence is **silently accepted** rather than rejected: `turn_start_gate` off → the hint bills on every request of a turn instead of the first; `pop` off → a one-shot rides forever. A truthy `hints` says the slot exists, never that a given behavior is implemented.

**`available` means the slot is REACHABLE, not that hints are working.** This is the one key on that object where truthy and operating come apart: every sibling capability (`stats`, `ping`, `prune`, …) has no registry, so a consumer generalizing correctly from them will read `hints: {available: true}` as "hints are live" and be wrong. The registry is empty at rest and `caps` describes only what you *could* register.

**"Is my hint actually live?" is irreducibly a `GET /_hints?agent=<your pattern>` call, and cannot be answered by the handshake.** Not an omission to be fixed later: hints are keyed by scope, so no global count on `/_identity` could tell you whether *your* route resolves anything — that is the `misconfigured`/`seen_agent` question, and it requires a request to have arrived, while `/_identity` spends nothing by contract. Two calls, two different facts: the handshake says the channel exists; the registry read says whether your scope resolves.

### `GET /_hints?agent=<pattern>&session=<id>`
Registry view. `agent_hints[]` / `session_hints[]` (each hint with `age_s`), `native_enabled`, `caps`, `enabled`, plus when `session=` is given: `effective[]` (the resolved set, most-specific-last) and `available_native[]`.

### `DELETE /_hints?agent=|session=[&id=<hint-id>]`
Drop one hint or the whole scope. **Idempotent**: clearing an absent hint is a success (`200`, `removed: 0`), so a caller can clear unconditionally without a read-modify-write.

## `placement` — the diagnostic (read this before concluding anything)

Returned on `GET /_hints?session=` and on the `turn.completed` receipt. It exists so an absent effect cannot read as a healthy one.

| state | shape | meaning |
|---|---|---|
| nothing arrived | `{"requests_seen": 0, "note": …}` | No request of this session reached the injector. **Not** proof hints work or don't. |
| working | `{"by_mode": {"user_tail": N, "system_tail": M}, "requests_with_hints": N+M, "fallback_exercised": bool, "declines": {}}` | `fallback_exercised: false` means this session tells you **nothing** about the `system_tail` path. |
| **misconfigured** | `{"misconfigured": true, "unmatched_requests": N, "seen_agent": "<wire name>", "note": …}` | Requests arrived, registry non-empty, **zero matched**. Always a misconfiguration — register `seen_agent` exactly, or a glob covering it. |

An **empty registry stays silent** (`requests_seen: 0`, no `misconfigured`): resting is not misconfiguration.

> **`placement` is NOT a deploy check, and cannot be made into one.** With an empty registry `inject()` returns before touching anything, so it cannot also increment a counter — "`requests_seen > 0` with an empty registry" is *unsatisfiable*, and it was briefly written down as the post-deploy verification anyway (clodex's, retracted by clodex). `requests_seen: 0` is also exactly what a wrong port, a dead proxy, or an unresolved route produces, so as a deploy check it collapses the very two states this block exists to separate.
> **Verify an empty-registry deploy without hint code at all:** `/_identity` reports the new `version` and advertises `capabilities.hints`, while `/_status` shows sessions with recent `last_seen` — the proxy's ordinary accounting proves requests are forwarding, and the injector's silence proves it didn't participate. Absence of placement state is the *correct* observation. `placement` can only ever confirm the **non-empty** case.

## Placement modes

- `user_tail` — appended after the last user message's blocks (the normal path).
- `system_tail` — appended after the deepest marker when that marker sits on the trailing `role:"system"` roster message (which would otherwise make a user-tail append land inside a cached segment). Wire-proven: 200/`end_turn`, cache reads intact, marked block unmutated. **This is the main path for Clodex traffic**, whose `SessionStart` hook emits that message by construction. Disable with `HINTS_SYSTEM_TAIL_FALLBACK=0` (declines then surface as `declined: "marker_downstream"` with `fallback_available`).

## Caps — declined, never truncated

A truncated hint reads as present and isn't, so an oversized set is **rejected whole** with a reason.

| env | default | meaning |
|---|---|---|
| `HINTS` | `1` | Kill switch. Registry is empty at rest, so enabled-and-unconfigured costs nothing. |
| `HINTS_MAX_ONE` | `2500` | Per-hint chars. |
| `HINTS_MAX_CHARS` | `3500` | Total across all hints on one request. |
| `HINTS_MAX_PER_SCOPE` | `16` | Registered hints per scope. |

`id` must match `^[a-z0-9][a-z0-9-]{0,63}$`. Duplicate ids in one request → 400.

**The two caps are checked in different places, so their RELATIONSHIP is the invariant, not their values.** `HINTS_MAX_ONE` must stay meaningfully below `HINTS_MAX_CHARS` — otherwise a hint passes the per-hint check and then fails the total, which is a limit that says yes and means no. (Live near-miss: a 2500-char catalogue requested against a 2000-char total would have cleared one gate and died at the next.) The headroom is deliberate: the total must fit the largest single hint **plus** a standing prohibition alongside it. A test asserts `HINTS_MAX_ONE < HINTS_MAX_CHARS` with room to spare — assert the relationship, never the two numbers separately.

**Overflow names its culprit.** The total is shared across scopes, which means one oversized hint can decline an unrelated standing prohibition. A bare "over cap" would attribute that to the injection rather than to the hint that ate the budget — the loud failure masking the silent one. So an over-total decline reports `over_cap` with `offender` (the hint id that pushed it over) and `dropped` (the ids that consequently did not ship). Per-scope budgets were considered and rejected: they move the effective ceiling invisibly as scopes come and go, whereas attribution fixes the actual defect.

## Conventions

HTTP status = **request validity**; outcome is in the body (`ok`). 400 only on malformed requests (missing `agent`/`session`, bad JSON, bad `mode`, cap violations, missing required `ttl_s`). Everything else is `200` with `ok` and a reason.

## Resting-state guarantee (the vendor gate)

**With an empty registry the proxy does not touch the request body at all** — not merely "no hint appended": no reshaping, no string→block conversion, no re-serialization. `inject()` returns `None` before touching anything, so the capture record gains no `tail_hints` key and the original bytes forward verbatim. Test-pinned byte-identical against 400 real captured bodies and verified on the wire. This matters because for every user who never configures a hint, the empty path *is* the product.
