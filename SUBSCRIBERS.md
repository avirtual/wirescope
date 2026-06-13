# wirescope subscriber protocol (v1)

> This is the **push-feed deep-dive**. For the whole integration surface (what
> the proxy offers, what to call, what it costs), start with the front-door
> contract: [`INTEGRATION.md`](./INTEGRATION.md).

The proxy sits on the wire between agent CLIs and the model backends, so it
sees things consumers can't get anywhere else, or can only get late:

- assistant text **as it streams** (before the CLI renders it, before the
  transcript JSONL is written);
- per-turn **usage and priced cost** from the response receipts (the CLI's own
  `total_cost_usd` under-reports; the wire is the bill);
- **cache state** (read/write split, warmth of the session's prefix);
- **refusals** (`stop_reason:"refusal"` is wire-only — the CLI shows a generic
  toast and the transcript shows nothing).

Any app can register an HTTP endpoint and receive these as push events for
the agent sessions it owns. The proxy knows nothing about your protocol or
your schema: if your agents emit `[wb:…]` or `[cli:…]` intents in their text,
*you* parse them from the text events. One subscriber API, zero app-specific
code in the proxy.

## Discovery: confirm this is the wirescope (`GET /_identity`)

Anything can sit on `ANTHROPIC_BASE_URL` in front of the model backend. Before
you register, pull stats, or warm a cache, confirm you're talking to *this*
proxy and not a generic forwarder:

    GET /_identity        → read-only, unauthenticated, spends nothing

    {
      "wirescope": true,                 // quick boolean for the lazy check
      "product": "wirescope",            // the authoritative discriminator
      "vendor": "proxy-lab",
      "version": "v0.2.4",              // release tag (or git-describe on a dev tree)
      "protocols": { "identity": 2, "subscribers": 1 },
      "capabilities": {                 // LIVE flags — gate features on these
        "subscribers": true, "warmth": true, "ping": true, "hold": true,
        "stats": true, "session_view": true, "codex": true
      },
      "endpoints": {                    // where each feature lives
        "identity": "/_identity", "status": "/_status", "subscribe": "/_subscribe",
        "warm": "/_warm", "ping": "/_ping", "end": "/_end",
        "admin": "/_admin", "session": "/_session"
      },
      "docs": "INTEGRATION.md"          // front-door contract; this file = push deep-dive
    }

- **Check `product == "wirescope"`** (or the `wirescope: true` shortcut). A
  different proxy will 404 `/_identity` or return a body without these fields —
  in either case, don't attempt wirescope-specific integration.
- The response also carries an `X-Wirescope-Version` header (handy if you only
  want a HEAD/sniff).
- `capabilities` are the *running* flags: a subsystem disabled by env reads
  `false`. Gate conditionally — e.g. only call `/_ping` when `ping` is true.
- Probe at the proxy ROOT (`http://127.0.0.1:7800/_identity`), same as
  `/_subscribe` — not under your `/agent/<name>/anthropic` route prefix.
- Additive-only, like the subscriber envelope: new fields won't bump
  `protocols.identity`; a breaking change will. Ignore unknown fields.

## 0. Prerequisite: route your sessions through the proxy with an agent name

Subscriptions match on **agent name**, which comes from the route. Launch
each session with:

    ANTHROPIC_BASE_URL=http://127.0.0.1:7800/agent/<name>/anthropic   # claude
    # codex: model provider base_url http://127.0.0.1:7800/agent/<name>/openai

Plain traffic (no `/agent/` prefix) is never pushed to anyone, regardless of
subscription patterns. The agent name is your addressing scheme — pick a
prefix per app (`wb-alice`, `clodex-bob`) and subscribe to the prefix.

## 1. Registering

    POST /_subscribe
    {
      "url":    "http://127.0.0.1:9000/api/proxy/events",   // your endpoint
      "name":   "workbench",                                // optional label
      "token":  "shared-secret",                            // optional; echoed back as Bearer
      "agents": ["wb-*"],                                   // glob patterns on agent name
      "events": ["text.delta", "turn.completed", "session.ended"]
    }

- **Upsert by `url`**: re-POSTing the same `url` replaces the registration
  (and reactivates it if it was suspended). Register on app startup,
  idempotently.
- Response: `{"ok": true, "id": "<subscriber-id>", ...the stored record}`.
- `agents` are shell-style globs (`fnmatch`) against the agent name.
- `events` is any subset of the three event types below.
- By default the proxy only accepts **loopback** callback URLs
  (`SUBSCRIBERS_ALLOW_REMOTE=1` lifts this).
- If the proxy was started with `SUBSCRIBERS_TOKEN=<secret>`, every
  `/_subscribe` call must carry `Authorization: Bearer <secret>`.
- The registration (including your `token`) is persisted in the proxy's local
  sqlite and survives proxy restarts.

Also:

    GET /_subscribe              → {"subscribers": [...]}  (tokens redacted)
    DELETE /_subscribe?url=...   → remove (or ?id=...)

## 2. Receiving events

The proxy POSTs one event per HTTP request to your `url`:

    POST <your url>
    Content-Type: application/json
    X-Wirescope-Event: <event type>
    Authorization: Bearer <your token>     (only if you registered one)

Reply with any 2xx. Body is always the same envelope:

    {
      "v": 1,
      "event": "turn.completed",
      "agent": "wb-alice",
      "session_id": "f3b2…",          // the CLI's session UUID (null if absent)
      "request_id": "017-103045",     // unique per proxied model request
      "ts": "2026-06-11T20:15:01Z",
      "data": { ... }                 // per event type, below
    }

### Delivery semantics (read this)

- **At-most-once, fire-and-forget.** No retries, no queue. A slow or dead
  subscriber never blocks or fails the agent's stream — delivery is
  best-effort by design.
- **No ordering guarantee** across events (deliveries are concurrent).
  `text.delta` carries `offset` so text reassembles without ordering;
  `turn.completed` carries the authoritative full text.
- **Suspension:** after 10 consecutive delivery failures (non-2xx, timeout
  >5s, connection refused) the subscription is suspended and stops receiving.
  Re-POST `/_subscribe` to reactivate. Suspended subscriptions still show in
  `GET /_subscribe` with `"suspended": true`.
- **Reconciliation is pull.** If you keep durable records (costs, turn
  counts), treat the push as a live feed and re-sync from
  `GET /_status?session=<id>` (durable, survives proxy restarts) whenever you
  reconnect or suspect a gap. Push for liveness, pull for truth.

## 3. Event reference

### `text.delta` — streaming assistant text

Normalized plain text (the model's prose; thinking/tool-call internals are
not included), already decoded from the provider's SSE dialect — identical
shape for anthropic and openai sessions. Deltas are coalesced (~300ms
flushes), not per-token.

    "data": {
      "provider": "anthropic",        // or "openai"
      "text": "…chunk of assistant text…",
      "offset": 1234                  // char offset of this chunk in the turn's text
    }

Reassemble by `offset`. The turn's text is complete when you receive the
matching `turn.completed` (same `request_id`) — subscribe to both if you need
an end-of-text signal. Intent grammars (`[wb:…]`, `[cli:…]`) are yours to
parse; if your grammar needs a "next intent started" boundary to know an
intent's body is final, run your parser over the accumulated text per delta
and treat all-but-last as final, last as final at `turn.completed`.

### `turn.completed` — per-request receipt (the ledger event)

Emitted once per model request, at response-stream end. Note "turn" here is
one API round-trip: a tool-using turn is several of these (each hop with
`stop_reason:"tool_use"`), ending with a terminal one (`"end_turn"`,
`"max_tokens"`, `"refusal"`). `turn_end: true` marks the terminal response of
a real user-visible turn (side-calls and subagent traffic are `false`).

Anthropic sessions:

    "data": {
      "provider": "anthropic",
      "model": "claude-fable-5",            // RESOLVED model from the wire
      "status_code": 200,
      "anthropic_request_id": "req_…",
      "message_id": "msg_…",
      "stop_reason": "end_turn",            // "tool_use" | "end_turn" | "max_tokens" | "refusal" | null
      "stop_details": null,                 // structured details (refusal category etc.)
      "refusal": false,                     // server-side classifier block; model never ran
      "turn_end": true,
      "role": "parent",                     // proxy's request classification: parent | sub | title | unknown…
      "title_call": false,                  // the CLI's title-generator side-call
      "text": "…full assistant text…",
      "tool_uses": ["Bash", "Read"],        // tool_use block names this response
      "usage": {                            // wire receipts, null when absent
        "input_tokens": 12,
        "output_tokens": 480,
        "cache_read_input_tokens": 154000,
        "cache_write_5m_tokens": 2100,
        "cache_write_1h_tokens": null,
        "cache_write_flat_tokens": null,    // fallback when the TTL split is absent
        "thinking_tokens": 120,
        "service_tier": "standard"
      },
      "cost": {
        "est_usd": 0.031,                   // proxy-priced from the receipts
        "unpriced": false                   // true = model not in the price table; est_usd null
      },
      "session_totals": {                   // running totals for this session
        "requests": 31, "turns": 7, "refusals": 0,
        "input_tokens": 1200, "output_tokens": 9100,
        "cache_read_tokens": 2400000, "cache_write_tokens": 310000,
        "est_usd": 1.27
      },
      "context": {                          // size of the context the CLI is carrying
        "turns_in_context": 7, "n_messages": 41,
        "max_tool_result_chars": 52000
      },                                    // null for side-calls/subagents
      "warmth": {                           // prompt-cache state of the session prefix
        "warm": true, "ttl_s": 300, "remaining_s": 281.5
      }                                     // ADVISORY: stamped off-thread, may lag one turn
    }

OpenAI/codex sessions (caching is server-side; cost is the API-EQUIVALENT
estimate — ChatGPT-plan traffic is never dollar-billed, the proxy prices the
same tokens at OpenAI API list rates so codex and anthropic carriage are
comparable in one ledger):

    "data": {
      "provider": "openai",
      "model": "gpt-5.1-codex",
      "status_code": 200,
      "response_id": "resp_…",
      "status": "completed",                // openai response status; their stop signal
      "text": "…full assistant text…",
      "usage": {                            // openai axes (input INCLUDES cached)
        "input_tokens": 9000, "cached_tokens": 8700,
        "output_tokens": 300, "reasoning_tokens": 120
      },
      "cost": { "est_usd": 0.0124, "unpriced": false },
      "session_totals": {                   // same shape as anthropic sessions
        "requests": 31, "turns": 7, "refusals": 0,
        "input_tokens": 1200, "output_tokens": 9100,
        "cache_read_tokens": 240000, "cache_write_tokens": 0,
        "est_usd": 0.41
      },
      "context": null, "warmth": null
    }

Fields are nullable: error responses, non-streaming bodies, and provider gaps
all deliver the event with whatever the wire actually carried.

### `session.ended` — the session is done

Fired when the CLI's SessionEnd hook calls the proxy's `/_end` (exit,
`/clear`, …). Best-effort: the proxy maps session→agent from traffic it has
seen since its own start.

    "data": { "reason": "clear" }           // "exit" | "clear" | "unspecified" | …

## 4. Worked example

    # 1. your app exposes POST /api/proxy/events and registers at startup:
    curl -s -X POST http://127.0.0.1:7800/_subscribe -d '{
      "url": "http://127.0.0.1:9000/api/proxy/events",
      "name": "workbench", "token": "s3cret",
      "agents": ["wb-*"],
      "events": ["text.delta", "turn.completed", "session.ended"]
    }'

    # 2. launch agent sessions through the proxy with your prefix:
    ANTHROPIC_BASE_URL=http://127.0.0.1:7800/agent/wb-alice/anthropic claude …

    # 3. events arrive at your endpoint; on reconnect/doubt, reconcile:
    curl -s 'http://127.0.0.1:7800/_status?session=<id>'

## 5. Proxy-side configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `SUBSCRIBERS` | `1` | Master switch for the whole subsystem. |
| `SUBSCRIBERS_ALLOW_REMOTE` | `0` | Allow non-loopback callback URLs. |
| `SUBSCRIBERS_TOKEN` | unset | If set, `/_subscribe` requires this bearer. |
| `SUBSCRIBERS_DELTA_MS` | `300` | Delta coalescing window. |
| `SUBSCRIBERS_MAX_FAILURES` | `10` | Consecutive failures before suspension. |

## Versioning

The envelope carries `"v": 1`. Additive changes (new fields, new event types
you didn't subscribe to) won't bump it; breaking changes will. Unknown fields
must be ignored.
