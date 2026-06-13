# wirescope integration contract

**Hand this file to any tool that wants to integrate the wirescope into its product.**
It is the front door: what the proxy gives you, what to call, and what each call costs.
The push-feed event schema lives in its own deep-dive, [`SUBSCRIBERS.md`](./SUBSCRIBERS.md); this file is the index over the whole surface.

## The deal

wirescope sits transparently on the wire between an agent CLI (claude / codex) and the model backend.
It forwards bytes verbatim — your traffic is unchanged — while observing what only the wire can see.
In return for routing through it you get:

- **the real per-turn bill** — priced usage from the response receipts (the CLI's own `total_cost_usd` under-reports);
- **prompt-cache warmth** — read/write split, whether the session prefix is warm and for how long;
- **refusals** — `stop_reason:"refusal"` is wire-only (the CLI shows a generic toast and the transcript shows nothing);
- **assistant text as it streams** — before the CLI renders it or writes the transcript;
- **the ability to keep a session's cache warm** between turns.

Cost to you: nothing on the byte path (capture is off-thread; a dead subscriber never blocks or fails an agent's stream).
The endpoints are **localhost, lab-grade, currently unauthenticated** — see Caveats.

## Step 1 — confirm it's us (`GET /_identity`)

Anything can sit on `ANTHROPIC_BASE_URL`.
Before you integrate, probe the proxy ROOT and check the product marker:

    GET http://127.0.0.1:7800/_identity        # read-only, unauthenticated, free

    { "product": "wirescope", "version": "...",
      "protocols": { "identity": 2, "subscribers": 1 },
      "capabilities": { "subscribers": true, "warmth": true, "ping": true,
                        "hold": true, "stats": true, "session_view": true,
                        "codex": true },
      "endpoints": { ... }, "docs": "INTEGRATION.md" }

- **Branch on `product == "wirescope"`.** A different proxy 404s `/_identity` or returns a body without these fields — don't attempt wirescope-specific calls.
- `capabilities` are the **live** flags of *this* process (env can disable a subsystem). Gate every feature on them — e.g. only call `/_ping` when `ping` is true. `endpoints` tells you where each one lives.
- Also returned as the `X-Wirescope-Version` response header (cheap sniff).

> **Deployed-state note (pre-`/_identity` builds, ≤ v0.2.7).** `/_identity` and the `capabilities` map post-date v0.2.7 — on those builds `/_identity` 404s and there is no `capabilities` object. Detect the proxy instead via `GET /_status` (200 with a `proxy.version` string), and read live subsystem state from `proxy.flags` (`hold`/`pinger`/`ledger`/`block_cold_ping`) and `proxy.subscribers.enabled`. Note also that on these builds `/_status.sessions` is a **list**, not a dict. Switch to the `product=="wirescope"` / `capabilities.*` handshake once the proxy reports a version that serves `/_identity`.

## Step 2 — route your sessions with an agent name

Everything session-scoped (subscriptions, stats, warming) keys off an **agent name**, which comes from the route.
Launch each session through:

    ANTHROPIC_BASE_URL=http://127.0.0.1:7800/agent/<name>/anthropic   # claude
    # codex: model-provider base_url http://127.0.0.1:7800/agent/<name>/openai

Pick a prefix per app (`wb-alice`, `clodex-bob`).
Plain traffic with no `/agent/` prefix is observed but **never** pushed to subscribers and is not your addressable namespace.
(The `/_*` control endpoints above are always at the proxy root, NOT under your route prefix.)

## The three ways to integrate

| Mode | Endpoint | Method | Cost | What you get |
|---|---|---|---|---|
| **Discover** | `/_identity` | GET | free | Is this our proxy + live capabilities (Step 1). |
| **Pull** | `/_status[?session=<id>][&all=1]` | GET | free | Durable JSON: sessions, titles/agent, model, cwd, per-session + global cost, turn counts, context size (`context.input_tokens` = the wire-measured input-side token count of the last turn — cache_read + cache_write + uncached input, the same figure `/_session` shows; plus `turns_in_context` / `n_messages`), warmth, hold, refusal events, and `cold_resumes` (lifetime count of turns that landed on a *lapsed* cache — each a full prefix re-write at the write premium; a high count flags a bursty session that keeps letting its cache go cold). **Survives proxy restarts** — this is your source of truth for reconciliation. |
| **Pull (human)** | `/_admin` | GET | free | HTML render of `/_status` (dark, auto-refresh). For eyeballs, not parsing. |
| **Pull (human)** | `/_session?session=<id>` | GET | free | HTML view of the session's captured context + last receipts + cache breakpoints. For eyeballs. |
| **Pull** | `/_warm?session=<id>` *(or `?h=<prefix-hash>`)* | GET | free | Just the warmth verdict for one session: `warm`/`found`, `remaining_s`, `ttl_s`. |
| **Act** | `/_ping?session=<id>[&force=1]` | GET/POST | **~1 token** | One-shot: replay the session's cached last request to slide the cache TTL once. Declines by default if the prefix is provably cold (`force=1` re-warms anyway). Spends credits. |
| **Act** | `/_hold?session=<id>&hours=<n>[&force=1]` | POST | free to arm | Arm **N hours of idle insurance**: the proxy auto-pings to keep the prefix warm until N hours after the session's last real turn (re-anchored on every turn). **Declines a cold/absent prefix** (`armed:false, skipped:<state>`) — arming over HTTP doesn't forward a turn, so there'd be nothing to keep warm (`force=1` arms anyway, e.g. when a turn is imminent). `hours=0` or `&action=off` disarms (never gated); `GET /_hold?session=<id>` reads current state. Arming is free; the auto-pings it schedules each cost ~1 token. The programmatic twin of the in-band `/warm-cache` command. |
| **Act** | `/_end?session=<id>[&reason=...]` | GET/POST | free | Mark a session finished (wire to your SessionEnd): disarms any hold, stops caching it, emits `session.ended` to subscribers. |
| **Push** | `/_subscribe` | GET/POST/DELETE | free | Register an HTTP endpoint + agent globs; receive `text.delta` / `turn.completed` / `session.ended`. **Full schema: [`SUBSCRIBERS.md`](./SUBSCRIBERS.md).** |

**Status-code convention.** HTTP status reflects whether the *request* was valid, not whether the side-effect happened. `2xx` = the proxy understood and processed it; read the body for the outcome (`ok`, and per-endpoint `armed` / `warmed`, with `skipped:<reason>` when the proxy deliberately did nothing — e.g. declining to warm a cold prefix). `4xx` is reserved for malformed/unfulfillable requests (missing `session`, bad `hours`). So **branch on the body's outcome fields, not on `2xx` alone** — a 200 can still mean "I chose not to act, here's why."

Rules of thumb:

- **Push for liveness, pull for truth.**
  The subscriber feed is at-most-once, fire-and-forget, no ordering, and suspends after repeated delivery failures.
  Whenever you reconnect or suspect a gap, reconcile from `GET /_status?session=` (durable).
  Don't treat the push as your ledger.
- **Keeping cache warm: three ways, pick by who's driving.**
  `POST /_hold?session=&hours=N` — arm once and let the proxy keep the prefix warm for N idle hours; the simplest programmatic option (you don't run the loop, the proxy does). Like `/_ping`, it **won't arm a cold/absent prefix** (replies `200` with `armed:false, skipped:<state>`): there's nothing to keep warm until a real turn establishes the cache — pass `force=1` to arm anyway. On success the reply's `pingable:true` confirms there's a replayable request + live auth to ping. Rely on those structured fields, not the human `ack` string.
  `GET/POST /_ping` — one-shot TTL slide if you'd rather own the cadence and cost yourself.
  `/warm-cache <hours>` — the bundled client's slash command; same hold as `/_hold`, but armed in-band by injecting a `<proxy:warm-cache hours=N>` sentinel into a forwarded turn (so it also donates auth + rewrites the cache). Use this when a human/agent is at the CLI; use `/_hold` from code.

## Caveats (read before you ship)

- **Localhost, unauthenticated, lab-grade.**
  No ACL today: any local app can register any agent glob and `/_ping` (which spends credits).
  Fine for a single user's machine; do not expose the proxy beyond loopback without adding a token gate.
  `/_subscribe` honours `SUBSCRIBERS_TOKEN` and loopback-only callbacks if the operator sets them; the rest are open.
- **Capabilities are runtime.**
  Re-read `/_identity` rather than assuming a subsystem is on — a given deployment may run with `SUBSCRIBERS=0`, `WARMTH_PINGER` off, etc.
- **Agent-routed traffic only.**
  Subscriptions and most per-session value require the `/agent/<name>/` route.
  `ext` (un-routed) traffic is observed for the operator's own `/_status` but is not yours to subscribe to.
- **Additive versioning.**
  New fields/events won't bump a protocol number; breaking changes will (`protocols.identity`, the subscriber envelope `v`).
  Ignore unknown fields.

## Where to look next

- **Push events, field by field:** [`SUBSCRIBERS.md`](./SUBSCRIBERS.md).
- **Live capabilities & version of a specific deployment:** `GET /_identity`.
- **Reconcile / source of truth:** `GET /_status?session=<id>`.
