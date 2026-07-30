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

`GET /_identity` → `capabilities.hints` = `{available, enabled, system_tail_fallback, native[], caps{}}`, and `endpoints.hints` = `/_hints`. **Gate on the capability, never on `version`.** Note `endpoints.hint` (no `s`) is an unrelated feature — the per-agent spawner-hint override.

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
| `HINTS_MAX_ONE` | `800` | Per-hint chars. |
| `HINTS_MAX_CHARS` | `2000` | Total across all hints on one request. |
| `HINTS_MAX_PER_SCOPE` | `16` | Registered hints per scope. |

`id` must match `^[a-z0-9][a-z0-9-]{0,63}$`. Duplicate ids in one request → 400.

## Conventions

HTTP status = **request validity**; outcome is in the body (`ok`). 400 only on malformed requests (missing `agent`/`session`, bad JSON, bad `mode`, cap violations, missing required `ttl_s`). Everything else is `200` with `ok` and a reason.

## Resting-state guarantee (the vendor gate)

**With an empty registry the proxy does not touch the request body at all** — not merely "no hint appended": no reshaping, no string→block conversion, no re-serialization. `inject()` returns `None` before touching anything, so the capture record gains no `tail_hints` key and the original bytes forward verbatim. Test-pinned byte-identical against 400 real captured bodies and verified on the wire. This matters because for every user who never configures a hint, the empty path *is* the product.
