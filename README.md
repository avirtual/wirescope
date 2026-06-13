# wirescope

**A transparent, analytical forward-proxy for the Claude Code and Codex CLIs.**
It sits on the wire between your agent CLI and the model backend, forwards every byte verbatim, and observes what only the wire can see — the real per-turn bill, prompt-cache warmth, refusals, and the assistant's text as it streams.
Point a CLI at it with one environment variable and nothing about your session changes, except that now you can *see* it.

```
   claude / codex CLI  ──►  wirescope (localhost)  ──►  api.anthropic.com
                              │                          chatgpt backend
                              ├─ prices every turn (the real bill)
                              ├─ tracks prompt-cache warmth + keeps it warm
                              ├─ surfaces refusals the CLI hides
                              └─ streams text + receipts to subscribers
```

## The thesis

**Cost is context carriage, not "the model thinking."**

In one measured 18-turn trivial session, context *carriage* cost \$2.72 while the actual generated output cost \$0.07 — a **38×** ratio.
A single "2+2" turn shipped 31 tools (~23k tokens) and used zero of them.

"It's cached, so it's cheap" is backwards: the right baseline for junk context is **zero**.
Caching bills ~10% of something that should not be there at all — every turn — and cached content still occupies bandwidth and the context window.
wirescope exists to **find and price that waste**, and to stay completely non-intrusive while doing it (disk I/O runs on a background thread; a dead observer never blocks or fails your stream).

## What you get

In return for routing through it:

- **The real per-turn bill** — priced from the response receipts. The CLI's own `total_cost_usd` under-reports (it misprices 1h-cache sessions); the proxy reconciles to the cent.
- **Prompt-cache warmth** — read/write split, whether the session prefix is warm, and for how long. Plus the ability to **keep a session's cache warm** between turns.
- **Refusals** — `stop_reason:"refusal"` is wire-only. The CLI shows a generic toast and the transcript shows nothing; the proxy captures the category and the context that triggered it.
- **Streaming assistant text** — normalized across both wire dialects, before the CLI renders it.
- **A capture of everything** — every request/response, for offline analysis (`analyze_tools.py` prices tool-set deadweight).

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

That's it — your session runs exactly as before, now fully observable.

> Prefer to manage the environment yourself? `pip install -r requirements.txt` (into a venv or with `pipx`/`--user`) and then `uvicorn logproxy:app --port 7800` works too.

## The biggest practical levers

The proxy proves where the waste is; most of the fix is native CLI flags you can adopt today:

- **Trim the tool set.** The CLI ships ~33 tools (~24k tokens *every turn*); a typical session uses ~4.
  `claude --tools "Read Edit Write Bash Glob Grep"` trims `tools[]` on the wire. Measured: **−72.7% tokens, −50% USD, same work**.
- **Inline files with `@file`.** `@path` inlines the file *and* primes the read-state, so the model edits directly instead of spending a Read turn. Measured A/B: **−47.6% tokens, −16% USD, half the latency**.
- **Replace the system prompt** with `--system-prompt-file` when you drive headless.
- Reserve the **proxy** for what needs the wire: turn-collapse, durable response mutation, cache-warmth management, and capture/analytics.

## Integrating a tool with the proxy

If you're building a tool that wants the proxy's data (cost, warmth, refusals, a live feed), start here:

- **[`INTEGRATION.md`](./INTEGRATION.md)** — the front-door contract. What the proxy offers, what to call, and what each call costs. Discovery via `GET /_identity`, session routing, and the full endpoint surface.
- **[`SUBSCRIBERS.md`](./SUBSCRIBERS.md)** — the push-feed deep-dive: register an endpoint and receive `text.delta` / `turn.completed` / `session.ended` events.

Confirm you're talking to wirescope (vs any other proxy on `ANTHROPIC_BASE_URL`) with `GET /_identity` → `{ "product": "wirescope", ... }`.

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
- **transforms** — request/response mutations (tool-sort, env relocation, cache-marker strips), all behind flags.
- **subs / canary / writer** — the subscriber push feed, a structural drift detector, and the background disk-writer thread.

The package `__init__` is lazy, so parts (e.g. just billing) can be imported standalone; the full lab boot is the `logproxy.py` entrypoint.

## Status & scope

This is **lab-grade, single-user, localhost** software.
The control endpoints are currently **unauthenticated** — fine for one person's machine, not for a shared host without adding a token gate.
It is built for research into context economics and cache mechanics, and as an integration substrate for local agent tooling.

## License

[MIT](./LICENSE) © Bogdan Ionescu
