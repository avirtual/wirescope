# wirescope integration contract

**Hand this file to any tool that wants to integrate the wirescope into its product.**
It is the front door: what the proxy gives you, what to call, and what each call costs.
The push-feed event schema lives in its own deep-dive, [`SUBSCRIBERS.md`](./SUBSCRIBERS.md); this file is the index over the whole surface.

## The deal

wirescope sits transparently on the wire between an agent CLI (claude / codex) and the model backend.
It observes what only the wire can see; by default it **also applies a few deterministic, cache-coherent transforms** to the request (alphabetize `tools[]`, relocate the volatile `# Environment` block to the tail, an operator `omit` floor) plus any `[wirescope:…]` directives you opt into.
Every transform is deterministic and flag-gated; run it with `WIRESCOPE_PASSTHROUGH=1` for a byte-for-byte forward (`capabilities.passthrough` tells you which mode a given deployment is in).
Either way, observation never alters or blocks the byte path.
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
      "protocols": { "identity": 2, "subscribers": 1, "wirescope": 1 },
      "capabilities": { "passthrough": false, "subscribers": true,
                        "warmth": true, "ping": true,
                        "hold": true, "stats": true, "session_view": true,
                        "codex": true,
                        "wirescope": { "agent_name": true, "omit": true,
                                       "replace": true, "keep": true,
                                       "spawn": true, "strip_tools": true,
                                       "omit_default": ["useremail"],
                                       "spawner_hint": false } },
      "endpoints": { ... }, "docs": "INTEGRATION.md" }

`capabilities.passthrough` is `true` when the deployment runs with `WIRESCOPE_PASSTHROUGH=1` (verbatim forward, all transforms off); `false` means the default transforms + directives are active.

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
| **Pull** | `/_context?session=<id>` | GET | free | **What's loaded for a session, per agent line** (main + each subagent). Returns `agents:[]`, each `{line:"main"\|"subagent", role, agent_id, display_name, model, wire, tools, skills, composition}`. **`tools`** = `{count, names, total_schema_chars, est_tokens, per_tool:[{name, schema_chars, est_tokens}]}` (`per_tool` biggest-first — the "what to trim" view). **`skills`** = the same shape for the CLI's injected skills list (`per_skill` instead of `per_tool`, biggest-first), parsed from the skills block on either wire shape; `null` when no skills are loaded or for a codex body. The skills trim lever is `skillOverrides:{name:"off"}` (`permissions.deny Skill(name)` is invocation-only — tokens still ship). **`composition`** = the per-category token breakdown of the whole context window — *what is taking up context*: `{total_tokens, basis, by_category:[{category, tokens, pct}]}`, `by_category` biggest-first. Category vocabulary (render generically, map any future addition to "other"): `system, claudemd, useremail, agents, skills, tools, user, assistant, thinking, tool_calls, tool_results` (file reads & command output land in `tool_results`; `claudemd`/`useremail` are split out so a consumer can attach the matching `[wirescope:omit …]` trim lever; `agents` = the CLI's injected agent roster and `skills` = its skills list — ~hundreds of tok/turn that would otherwise hide inside `system`. The proxy detects both wire shapes (the older `<system-reminder>`-wrapped block in `messages[0]`, and the opus-4-8 `mid-conversation-system` form where the roster + skills arrive concatenated in a trailing `role:"system"` message). Trim levers differ: skills reclaim via `skillOverrides:{name:"off"}` in settings (`permissions.deny Skill(name)` is invocation-only — tokens still ship); the agent roster is effectively a fixed cost in the CLI/PTY path (built-ins always present on the wire), so treat `agents` as visibility, not an actionable trim. **`skillOverrides:{name:"off"}` is wire-confirmed to bank the tokens** (measured: skills composition 925→666 tok, roster 8→7 with the disabled skill gone — the `deadweight_tokens` you compute is exactly this reclaim). ⚠️ **It only takes effect on a FRESH conversation eval**: the skills roster is frozen at session *creation*, so `--resume`/restart replays the old roster and the override does NOT apply — a `/clear` (or a non-resume start) is required for a skills `skillOverrides` change to land. (This is skills-specific; `permissions.deny` for tools enforces per-invocation and works on resume.) `basis` = `"receipt"` on the main line (categories scaled to the real wire-measured `input_tokens`, so the breakdown sums to the same total `/_status` reports) or `"estimate"` on subagents (char-derived, no usage receipt). Rosters & composition are the **actually-forwarded** content (post-wirescope-trim). In-memory only: a cold/ended session returns `agents:[]` + a `note`. Codex/openai-wire entries report `wire:"openai"`, `tools:null`, `composition:null`. |
| **Pull** | `/_context?session=<id>&utilization=1` | GET | cheap (one disk scan) | **Did the loaded tools pay off?** Opt-in extension of `/_context` — adds tool *usage* to each agent line so you can spot deadweight (loaded every turn, never called). Off by default (a per-session capture-dir scan, so keep it off the fast poll; fetch on-demand, e.g. when a user opens a context popover). When on: each `tools.per_tool[]` entry gains **`used`** (raw invocation count over the session — 3 Reads in one turn = 3), `per_tool` is re-sorted **deadweight-first** (never-used first, then biggest schema = the trim list), and each agent gains **`utilization`** = `{basis:"capture-scan", evaluable_turns, loaded, used_distinct, deadweight_tokens}`. `deadweight_tokens` = the per-turn schema carriage of currently-loaded tools that were never called — the concrete "free up ~N tokens" payoff to attach the trim lever (`--tools` for the main agent, `[wirescope:strip-tools …]` for subagents). `evaluable_turns` = tool-loading turns that actually ran (200) — read `used:0` against it for confidence (`0/2` inconclusive, `0/40` dead). The same scan also prices **skills** the same way: each `skills.per_skill[]` gains `used` (skill invocations, counted from assistant `Skill` tool_use blocks by `input.skill`, deduped across the history that re-ships them), re-sorted deadweight-first, plus a per-agent **`skills_utilization`** rollup of the same shape — `deadweight_tokens` there is the `skillOverrides:{name:"off"}` reclaim payoff. Scoped to the live `session_id` (one capture dir → never spans a `/clear`). Gate on `capabilities.context_utilization` (skills usage rides the same flag); turns captured before this shipped lack per-subagent attribution (they fold into the main line). |
| **Pull** | `/_report?session=<id>[&detail=1]` | GET | cheap (one disk scan) | **Where did this session's tokens and dollars actually go — and is it optimal?** The on-demand, **disk-based** cost/efficiency report (works on **ended/historical** sessions, unlike the in-memory `/_context`). Heavy (scans the whole capture dir) → on-demand only, e.g. behind a "Full report →" link, not the poll loop. Returns a **versioned** payload (`report_version`, the coupling surface) — the quantitative truth (pricing, cache math, thresholds, verdict) stays here so every surface agrees; you render prose. Top-level: `scope` · `totals` (summed per-request billing — **NOT** the global `cumulative`) · **`cost_decomposition`** = where the *dollars* went in real cache rates: `by_bucket:[{bucket, usd, pct, tokens}]` over `cache_read` (the preamble tax — typically the bulk), `cache_write_initial`, `cache_write_rewrite`, `uncached_input`, `output`, plus **`cache_misses`** = `{count, usd, tokens, where, by_cause, events:[{where:"preamble"\|"conversation", suspected_cause:"idle_gap_gt_ttl"\|"eviction", idle_gap_s, usd, tokens}]}` (a *localised drill-down* of the `cache_write_rewrite` bucket — already counted there, NOT an extra addend) · **`token_decomposition`** = where the *tokens* go (char-estimate), with `preamble:{tokens_per_turn, turns_resent, total_resent_tokens, used_tokens_per_turn, unused_tokens_per_turn, by_category}` · **`findings`** = flat, sorted by `reclaimable_usd` desc, each `{id, category, line, title, detail, reclaimable_tokens(_per_turn), reclaimable_usd, turns, evidence, confidence:"high"\|"medium"\|"low", additive, lever}` (price honestly: deadweight rides cache as a 0.10× read every turn + one write, not full input rate; the `cache_misses` finding's `reclaimable_usd` is **net/marginal** — gross write minus the 0.10× read you'd pay even kept warm, with `evidence.gross_write_usd`/`read_equiv_usd` shown) · **`waste`** = the consolidated *"what could have gone better"* — the **avoidable subset** of cost priced as the real *net* saving, aggregated by type across all agent lines: `{total_usd, pct_of_session, by_type:[{type, usd, tokens, items, confidence, lever}], basis}`. Types: `cold_cache` (marginal), `deadweight_tools`, `deadweight_skills`, `claudemd_carriage`, `useremail_carriage`. **`waste.total_usd == verdict.reclaimable_usd_total`**. Distinct from `cost_decomposition` (which is *gross* — every dollar actually paid); `waste` is what better choices reclaim. · **`verdict`** = `{rating:"optimal"\|"suboptimal"\|"wasteful", score:0-100 (higher=better; driven by high+medium *additive* reclaimable only — low-conf never moves it), headline (plain factual sentence default), reclaimable_usd_total, reclaimable_pct, confidence}` · **`invariants`** (assertable: `Σ by_bucket.usd == totals.est_usd`; `preamble.unused_tokens_per_turn == Σ deadweight findings`; `cache_misses.usd == cache_write_rewrite bucket`). Group reclaimable by each finding's `lever` (trim-config / keep-warm / omit) for the render. Low-conf findings (`redundant_tool_calls`, `cheaper_tool_available`) are `additive:false`, score-excluded → render dimmed. `detail=1` reserved for v1.1 per-turn series. Cold/unknown session → `scope.requests:0` + `note`. Gate on `capabilities.context_report`. |
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

## Shape the context on the wire — directives (`wirescope:`)

Beyond the HTTP endpoints, wirescope honors an in-band control channel: **`[wirescope:...]` directives** the proxy reads off a request, acts on, then **strips before forwarding** — so the model never sees them and they cost zero prefix tokens.
This is how you trim or rewrite the context the CLI auto-injects (project `CLAUDE.md`, the user's email) — things the wire otherwise gives no knob for.

There are two placements, and **an integrating app cares most about the second**:

- **Body directives** — written into an agent's `.claude/agents/*.md` body. A property of the agent *type*; the agent's author opts in.
- **Spawn directives** — written at the **strict head of the prompt you hand a spawn**. A property of the *call*: your app can shape any spawn's context **without editing the agent**, including uneditable built-ins (Plan / Explore / general-purpose). Just lead the spawned prompt with the directive(s). They **persist for the whole life of that subagent instance** — the proxy remembers them per instance (keyed by the `x-claude-code-agent-id` it sees on the wire) and re-applies them on every later turn, even after the spawn prompt scrolls out of `messages[0]` (a follow-up, a `/clear`, or a compaction summary). You set it once at spawn; you do not re-send it each turn.

The verbs:

| Directive | Effect |
|---|---|
| `[wirescope:agent-name <label>]` | Give the (otherwise nameless) subagent a display label in `/_status` + `/_admin`. |
| `[wirescope:omit claudemd,useremail]` | Drop the `# claudeMd` / `# userEmail` sections from the first user message — reclaims tokens (claudeMd is usually the bulk) and reduces fable refusal-classifier hits. |
| `[wirescope:replace claudemd <inline text>]` | Keep the section heading but swap its body for a one-line substitute (e.g. point the agent at a leaner doc). Single-line; for more, `omit` and write your own. |
| `[wirescope:keep <targets>]` | Override verb — cancel an `omit`/`replace` a body default would apply (precedence: spawn > body). |
| `[wirescope:tools Read,Glob]` | Allowlist a subagent's tool roster — the biggest per-turn lever (the ~33-tool / ~24k-token default that native `--tools` can't reach for a subagent). `strip-tools <names>` is the denylist variant, `keep-tools <names>` the override. Gated by `WS_STRIP_TOOLS`. |

Example — spawn a stock `general-purpose` subagent but strip the project CLAUDE.md and email from *this* call:

    [wirescope:omit claudemd,useremail]
    Search the repo for all callers of foo() and summarize.

**Feature-detect before relying on it:** `capabilities.wirescope` (from `/_identity`) reports `{agent_name, omit, replace, keep, spawn, strip_tools}` as live booleans (a deployment can disable `omit`/`replace` via `WS_OMIT=0`, the tool-roster verbs via `WS_STRIP_TOOLS=0`, or spawn-position reading via `WS_SPAWN_DIRECTIVES=0`). It also carries two operator-configured fields:

- `omit_default` — the list **already stripped from every subagent spawn** with no directive at all (e.g. `["useremail"]`). Check it before adding your own `omit`; the universal case may already be handled.
- `spawner_hint` (bool) — when on, the proxy appends one self-contained line to spawn-capable **main** agents pointing them at this directive grammar. It's a discovery aid the *agent* sees, not something you call; if your app already teaches its agents the grammar you can ignore it.

`protocols.wirescope` is the grammar version. `omit`/`replace`/`keep` only *do* something where the directive is present — no directive, no change.

**Full grammar, safety model, and the omit-target registry: [`WIRESCOPE.md`](./WIRESCOPE.md).**

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
- **Wirescope directives (`agent-name` / `omit` / `replace` / `keep`):** [`WIRESCOPE.md`](./WIRESCOPE.md) — the full grammar + safety model behind the "Shape the context on the wire" section above (v1: renamed from `ws:`, adds per-call spawn directives, `keep`, and `replace`).
- **Live capabilities & version of a specific deployment:** `GET /_identity`.
- **Reconcile / source of truth:** `GET /_status?session=<id>`.
