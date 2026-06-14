# wirescope

**A transparent, analytical forward-proxy for the Claude Code and Codex CLIs — that can also reshape the wire.**
It sits between your agent CLI and the model backend, observing what only the wire can see (the real per-turn bill, prompt-cache warmth, refusals, the assistant's text as it streams) and, when you ask it to, **trimming the context the CLI sends** — stripping `CLAUDE.md` from a subagent, cutting a bloated tool roster, keeping a session's cache warm.
Point a CLI at it with one environment variable. Observation is automatic and invisible; the payload-shaping is opt-in and deterministic.

```
   claude / codex CLI  ──►  wirescope (localhost)  ──►  api.anthropic.com
                              │                          chatgpt backend
                              ├─ prices every turn (the real bill)
                              ├─ tracks prompt-cache warmth + keeps it warm
                              ├─ surfaces refusals the CLI hides
                              ├─ streams text + receipts to subscribers
                              └─ reshapes the request on opt-in directives
```

## Why it exists — the thesis

**Cost is context carriage, not "the model thinking."**
In one measured 18-turn trivial session, context *carriage* cost \$2.72 while the generated output cost \$0.07 — a **38×** ratio. A single "2+2" turn shipped 31 tools (~23k tokens) and used none of them.
"It's cached, so it's cheap" is backwards: the right baseline for junk context is **zero**, and caching still bills ~10% of it every turn while it occupies bandwidth and the context window.

wirescope was built first to **find and price that waste** — and then to **act on it on the wire**, in the one place a CLI's own knobs can't reach: a *subagent's* context. Native `--tools` and `omitClaudeMd` only touch the main agent; a spawned subagent's roster and `CLAUDE.md` are frozen in its definition. wirescope reconstructs those missing knobs as opt-in directives (see below).

## What you get (observation — automatic)

Routing through it, with no directives and no config:

- **The real per-turn bill** — priced from the response receipts. The CLI's own `total_cost_usd` under-reports (it misprices 1h-cache sessions); the proxy reconciles to the cent.
- **Prompt-cache warmth** — read/write split, whether the session prefix is warm and for how long, plus the ability to **keep a session's cache warm** between turns.
- **Refusals** — `stop_reason:"refusal"` is wire-only. The CLI shows a generic toast and the transcript shows nothing; the proxy captures the category and the context that triggered it.
- **Streaming assistant text** — normalized across both wire dialects, before the CLI renders it.
- **A capture of everything** — every request/response, for offline analysis (`analyze_tools.py` prices tool-set deadweight).

This side is non-intrusive by construction: capture I/O runs on a background thread, and a dead observer never blocks or fails your stream.

## What you can do — the wirescope directives (opt-in)

This is the namesake feature. You annotate **an agent's `.md` body** (a property of the agent *type*) or **the head of a spawn's prompt** (a property of the *call*) with `[wirescope:…]` directives; the proxy reads them, reshapes the request on the wire, and **strips the directive lines before forwarding** so the model never sees them and they cost zero prefix tokens.

| Directive | What it does |
|---|---|
| `[wirescope:omit claudemd,useremail]` | Strip those context sections from the wire — the generalized `omitClaudeMd`, including `userEmail`, which nothing native can remove. |
| `[wirescope:replace claudemd <text>]` | Keep the section heading, swap its body for your lean inline text. |
| `[wirescope:keep claudemd]` | Override verb — cancel a lower-layer omit/replace for one target. |
| `[wirescope:tools Read,Edit,Grep]` | Allowlist a subagent's tool roster (the ~33-tool / ~24k-token-per-turn lever native `--tools` can't reach for a subagent). |
| `[wirescope:strip-tools Bash]` | Denylist — remove named tools, keep the rest. |
| `[wirescope:agent-name <label>]` | A human display label for the subagent in `/_admin` / `/_session`. |

```
# lead a spawn's prompt with directives to customize an unmodified built-in agent:
[wirescope:omit claudemd,useremail]
[wirescope:tools Read,Edit,Grep]
<the actual task text…>
```

Directives are read **only** from the system body or the strict head of a spawn's prompt — never from arbitrary message content, so they can't be forged downstream — and are **sticky per subagent instance** (they persist past turn 1). An operator can set a deployment-wide floor (`WS_OMIT_DEFAULT=useremail`), and a default-off **spawner hint** (`WS_SPAWNER_HINT`) can teach the syntax to spawn-capable agents on the wire. The full grammar, precedence, and cache semantics are in **[`WIRESCOPE.md`](./WIRESCOPE.md)**.

> **It's deterministic, not verbatim.** Even with no directives, wirescope applies a few cache-coherent default transforms (alphabetize `tools[]`, relocate the volatile `# Environment` block to the tail, strip the `# Session-specific guidance` system section) — each deterministic so spawns of one agent still share a cached prefix. All of it is behind flags; set the kill-switches (`WS_OMIT=0`, `WS_STRIP_TOOLS=0`, `SORT_TOOLS=0`, `RELOCATE_ENV_TO_TAIL=0`, `STRIP_SYSTEM_SECTIONS=''`) for a byte-faithful forward.

## Quick start

Needs Python 3.9+. The only third-party deps are `httpx`, `starlette`, and `uvicorn`.

```bash
# 1. Run the proxy (defaults to :7800, captures into ./logs_main)
./start_proxy.sh
```

On a fresh clone `start_proxy.sh` notices the deps are missing and **bootstraps a self-contained `./.venv` for you** (via `./setup.sh`) before launching — no manual `pip` step, and nothing touches your system Python (so you never hit the "externally-managed-environment" / PEP 668 wall). If you'd rather set things up first, run `./setup.sh` on its own; if you already have the deps on your own interpreter (or an active venv), it's used as-is and nothing is built.

```bash
# 2. Point a CLI at it
ANTHROPIC_BASE_URL=http://localhost:7800 claude

# 3. See what's happening
curl -s localhost:7800/_status | jq .      # JSON: per-session cost / warmth / context
open  http://localhost:7800/_admin         # live HTML dashboard (auto-refresh)
```

Your session runs as before, now fully observable — and ready to shape with directives when you want to.

> Prefer to manage the environment yourself? `pip install -r requirements.txt` (into a venv or with `pipx`/`--user`) and then `uvicorn logproxy:app --port 7800` works too.

## The biggest practical levers

The proxy proves where the waste is; some of the fix is native CLI flags, and some is wirescope reaching where they can't:

- **Trim the tool set.** The CLI ships ~33 tools (~24k tokens *every turn*); a typical session uses ~4. For the **main** agent, `claude --tools "Read Edit Write Bash Glob Grep"` trims `tools[]` natively — measured **−72.7% tokens, −50% USD, same work**. For a **subagent** (which native flags can't reach), `[wirescope:tools …]` does the same on the wire.
- **Drop dead context from subagents.** `[wirescope:omit claudemd,useremail]` strips the project `CLAUDE.md` and account email a spawned helper usually doesn't need — including `userEmail`, which nothing native removes.
- **Inline files with `@file`.** `@path` inlines the file *and* primes the read-state, so the model edits directly instead of spending a Read turn. Measured A/B: **−47.6% tokens, −16% USD, half the latency**.
- **Replace the system prompt** with `--system-prompt-file` when you drive headless.
- **Keep the cache warm** across idle gaps with `/_hold` / `/warm-cache` instead of paying a cold prefix write on the next turn.

## Integrating a tool with the proxy

If you're building a tool that wants the proxy's data (cost, warmth, refusals, a live feed) or wants to shape the wire, start here:

- **[`INTEGRATION.md`](./INTEGRATION.md)** — the front-door contract. What the proxy offers, what to call, and what each call costs. Discovery via `GET /_identity`, session routing, and the full endpoint surface.
- **[`WIRESCOPE.md`](./WIRESCOPE.md)** — the directive grammar: omit / replace / keep / tools / agent-name, placement, precedence, and cache semantics.
- **[`SUBSCRIBERS.md`](./SUBSCRIBERS.md)** — the push-feed deep-dive: register an endpoint and receive `text.delta` / `turn.completed` / `session.ended` events.

Confirm you're talking to wirescope (vs any other proxy on `ANTHROPIC_BASE_URL`) with `GET /_identity` → `{ "product": "wirescope", ... }`. Capabilities (including the live `wirescope` directive set) are advertised there for feature-detection.

### Endpoint surface (all localhost, read-only unless noted)

| Endpoint | What it gives you |
|---|---|
| `GET /_identity` | Product/version/live-capabilities handshake. |
| `GET /_status[?session=]` | Durable JSON: per-session cost, warmth, context, model, turns, refusals. Survives restarts. |
| `GET /_admin` | Live HTML dashboard (warm/cold tables, subagents, refusals). |
| `GET /_session?session=` | HTML view of a session's captured context + cache breakpoints. |
| `GET /_warm?session=` | Just the warmth verdict for one session. |
| `GET/POST /_ping?session=` | Slide a session's cache TTL once (~1 token). |
| `POST /_hold?session=&hours=N` | Arm N hours of idle keep-warm insurance. |
| `GET/POST/DELETE /_subscribe` | Register for the push feed (see SUBSCRIBERS.md). |
| `GET/POST /_end?session=` | Mark a session finished (wire to SessionEnd). |

## Client integration

[`client/`](./client) ships the pieces a Claude Code session uses to *talk* to the proxy — a `/warm-cache` slash command, a statusline that shows cache warmth + cost, and cache-expiry/cache-state hooks.
Everything fails soft: proxy down ⇒ statusline renders `cache ∅`, hooks exit 0.
See [`client/README.md`](./client/README.md).

## Architecture

`logproxy.py` is a thin shim; the implementation is the `proxylab/` package (each module owns its own job and its own SQLite tables):

- **core / store** — identity, routes, the shared HTTP client; the shared SQLite connection + schema registry.
- **server** — routing, the transform chain, and streaming.
- **billing / receipts** — usage parsing, pricing (Anthropic + OpenAI), and the single turn-finalize convergence point.
- **warmth / pinger / hold** — prompt-cache warmth ledger, TTL-slide replay, and idle keep-warm holds.
- **transforms** — request/response mutations (wirescope directives, tool-sort, env relocation, cache-marker strips), all behind flags.
- **subs / canary / writer** — the subscriber push feed, a structural drift detector, and the background disk-writer thread.

The package `__init__` is lazy, so parts (e.g. just billing) can be imported standalone; the full lab boot is the `logproxy.py` entrypoint.

## Status & scope

This is **lab-grade, single-user, localhost** software.
The control endpoints are currently **unauthenticated** — fine for one person's machine, not for a shared host without adding a token gate.
It is built for research into context economics and cache mechanics, and as an integration substrate for local agent tooling.

## License

[MIT](./LICENSE) © Bogdan Ionescu
