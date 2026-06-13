# proxy-lab — handoff for the next session

## ⚡ Ephemeral handoff state → `HANDOFF.local.md` (gitignored)

@HANDOFF.local.md

Session-end NOTE TO SELF (what shipped, what's live, NEXT build target,
carryovers) lives in **`HANDOFF.local.md`** — local-only, gitignored, imported
above. **Update THAT file at session end, not this one.** CLAUDE.md = durable
conclusions + how to run; commit only when those change. If `HANDOFF.local.md`
is missing (fresh clone), recreate it from this convention.

This file is a HANDOFF, not an archive. Full history with every experiment and
rationale: `CLAUDE.md.archive-2026-06-09` (the early era) and
`CLAUDE.md.archive-2026-06-11` (the detailed version of THIS file + the
2026-06-10/11 handoff notes). Read them only to re-derive *why* a conclusion
holds or for verbatim flag-mechanics detail.

## Doc style (user preference)

**Do NOT hard-wrap prose at 80 cols in NEW docs** (INTEGRATION.md, etc.) — break
at natural sentence/clause ends, let the editor soft-wrap. (Legacy files here —
this CLAUDE.md, SUBSCRIBERS.md — are still hard-wrapped from before; match a
file's existing style when editing it, but author anything new sentence-per-line.)

## What this is

`logproxy.py` = a transparent analytical forward-proxy between the `claude` CLI
and `api.anthropic.com` (plus codex/ChatGPT-backend routing). Point the CLI at
it via `ANTHROPIC_BASE_URL`. Forwards bytes verbatim, captures everything.
**Mission: find & price context waste; stay non-intrusive.** Disk I/O on a
background writer thread; the handler only parses + enqueues.

Driving experiments live in `/Users/bogdan/projects/proxy-experiments/` (own
CLAUDE.md). Only coupling: the proxy port + `LOG_DIR`.

**Code layout (since 2026-06-11): `logproxy.py` is a thin compat shim
(uvicorn `logproxy:app` + `import logproxy` keep working); the real code is
the `proxylab/` package — see "Module map" below. Zoom into the module you
need; don't load the whole thing.**

## The thesis (proven on the wire)

**Cost is context carriage, not "the model thinking."** 18-turn trivial
session: carriage = $2.72, actual output = $0.07 — 38×. "Cached so it's cheap"
is backwards: the right baseline for junk context is ZERO; cache bills 10% of
something that should be 0, every turn, and cached content still occupies
bandwidth + context window. A "2+2" turn loaded 31 tools (~23k tok), used 0.

## Biggest practical levers (native first; proxy only for what needs the wire)

- **Tool-set trimming:** default CLI ships ~33 tools ≈ 24k tok every turn; a
  normal session uses ~4. `claude --tools "Read Edit Write Bash Glob Grep"`
  (trims tools[] on the wire; `--allowedTools` only gates permissions) +
  `CLAUDE_CODE_DISABLE_WORKFLOWS=1` (Workflow alone = 5.2k tok). Measured:
  −72.7% tokens, −50% USD, same work.
- **`@file`** inlines the file AND writes `readFileState` → 0 Reads, Edit runs
  directly. A/B 2026-06-11: −47.6% tokens, −16% USD, half the latency vs
  letting the model Read. (A bare path without `@` does NOT help.)
- **`--system-prompt-file`** natively REPLACES the whole agent-prompt block
  (verified on wire); billing header + 62-ch SDK preamble + msg0 context
  bundle still ship. Beats proxy system-editing for headless drivers.
- **`--exclude-dynamic-system-prompt-sections`** = the CLI's native version of
  our `RELOCATE_ENV_TO_TAIL`. NOT yet A/B'd against ours — do that before
  trusting ours on.
- Reserve the PROXY for: turn-collapse, durable response mutation, warmth
  management, capture/analytics.

## Hard facts (don't re-derive; full detail in the archives)

**Cache mechanics**
- Prefix-based, content-addressed, byte-exact. Tail edits cache-safe;
  system/tools/history edits bust downstream. Canonical order tools → system →
  messages regardless of JSON key order. ≤4 `cache_control` breakpoints, each
  caches the cumulative prefix. Typical: M1 tools+preamble, M2 system prose +
  `# Environment`, M3 messages.
- **CLAUDE.md is USER space** (a `<system-reminder>` block in `messages[0]`),
  auto-loaded from cwd → run clean experiments from a neutral cwd.
- **TTL: 5m default; 1h is a CLI override on the main agent only** (subagents
  5m). Sliding idle timer, resets on every read. Write premium 5m=1.25×,
  1h=2×; reads 0.10×. **Choose by reuse-gap:** <5m→5m, 5m–1h→1h, >1h→neither;
  ephemeral one-shots → 5m, persistent brains → 1h; default 5m when unsure.
  Mid-turn eviction is real (slow tool step re-writes the prefix); 1h's real
  value = shielding slow in-flight pauses.
- **CLI `total_cost_usd` UNDER-reports 1h sessions** (prices writes at the 5m
  rate; reconciled to the cent). The PROXY number is the real bill.
- **Custom `ANTHROPIC_BASE_URL` flips the client to 3P → org cache scope** →
  static/dynamic system boundary never inserted; everything we measure is this
  degraded org path. The real env-buster is CWD (the `# Environment` block).
- The read-before-edit gate is CLIENT-side (`readFileState`, never on the
  wire) → a transparent proxy CANNOT forge it. `@file` is the clean answer.
- **Response text mutation persists** into the transcript (CLI does no
  signature check on text — never touch THINKING blocks, they're signed).
  Request injection is transient and desyncs history.
- **/compact** is not a `-p` text command — drive it via
  `--input-format stream-json` (one user line); emits `compact_boundary` with
  pre/post tokens. `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=100` disables auto.
  To drive a live multi-turn stream-json session you MUST hold stdin open
  (FIFO + held write fd); EOF alone doesn't terminate `claude -p` stream-json.
- `/clear` DOES fire SessionEnd (reason "clear") and rotates the session_id.

**Warmth / compact economics (condensed verdicts)**
- Warmth lives on the PREFIX LINEAGE, not the session_id; the proxy LEARNS it
  from response receipts (`cache_creation>0 or cache_read>0`) into a shared
  SQLite ledger (`warmth.sqlite`, WAL, all ports). TWO-STATE: warm = row
  exists AND `expires_at > now`; absence ≈ expiry (durable receipt-confirmed
  store makes that honest). Correctness lives in the read predicate; the
  sweeper is hygiene-only. Gates: **ping IFF warm; strip IFF not-warm;
  can't-judge ⇒ decline both.**
- Compact-on-busted-cache: compact wins the first turn iff `read + 5S <
  premium·H` → S<0.20·H at 1h, S<0.05·H at 5m; typical summaries (5–15%) win
  ~immediately at 1h. `STRIP_COMPACT_CACHE` (warmth-gated) reclaims the
  discarded write premium on a cold compact; stripping a WARM compact is a
  big loss — hence the gate.
- Ping economics: at 1h, one warm read buys an hour ≈ 19:1; at 5m it's a bad
  bet. Hold caps pings (24) + clamps duration (12h).

**SHORTCIRCUIT (SC) — elide the "Done." wrap-up turn** (all priors 4.x!)
- Synthetic end_turn on a successful terminal edit whose dispatch text carried
  the sentinel. Use canned-ack + SYSPATCH delivery (system prompt, cache-rides;
  tool-description delivery is ignored by models). Blunt prohibition + worked
  exemplar → sonnet 3/3 trivial AND build, −49.5% tokens; gentle phrasing 0/3.
  Only sonnet front-loads; opus/haiku DEFER under adaptive thinking. A miss is
  free. Re-validate on fable before relying on it.

## Module map (proxylab/ package)

Split 2026-06-11; made library-grade 2026-06-12. **The package `__init__` is
LAZY** (`from proxylab import billing` boots only billing+core+writer — no
sweeper/restore/app, so other projects can lift parts); the **full ordered
lab boot is the `logproxy.py` shim** (uvicorn entry + tests; eager, monolith
order). The shim resolves any name lazily (PEP 562), so rebindable globals
read live; to FLIP a flag from outside, assign on the owning module
(`lp.warmth.WARMTH_LEDGER = …`), not on the shim. **Growth rules (enforce in
review):** core+store import nothing from the package; a module creates ONLY
ITS OWN tables (store's schema registry) and mutates only its own globals;
server/receipts may know everyone, nobody but the shim knows server. Modules
(size · job):
core 5k (identity/constants/routes/httpx client) · store 4k (**shared SQLite:
connection + LOCK + OWNER + schema registry; each module registers its own
DDL**) · codex 9k (openai provider + zstd capture) · transforms 51k
(inject/SC/relocate/strip/sort/compact-strip + gates) · canary 8k (drift
detector) · writer 6k (disk-writer thread, _classify_role, NO_SESSION) ·
warmth 14k (prefix hashing + ledger semantics; store plumbing moved out) ·
subs 21k (subscriber push feed; contract in SUBSCRIBERS.md; owns
`subscribers`) · meta 9k (session identity + turn stats, _ENDED/
_CONTEXT_STATS/_LAST_RESPONSE/_LAST_USAGE/_SUBAGENTS; owns `session_meta`;
**subagents share the parent's session_id — main-line-only writes guard the
identity row, sub activity tracked separately in _SUBAGENTS**) · pinger 22k
(last-request cache, _ACCOUNT_AUTH, /_ping replay, teardown + sweeper; owns
`last_request`) · hold 22k (/warm-cache driver, echo transform, auth
bootstrap; owns `hold_state`) · billing 17k (usage parse, PRICES/
PRICES_OPENAI, totals) · **receipts 11k (the ONE turn-finalize convergence
both wires call: bill → view state → ledger stamp → capture → subscriber
receipt → console; new "a turn happened" consumers go HERE, never into
server's closures)** · restore 8k (restart-amnesia) · status 7k (/_status
snapshot) · views 28k (/_admin + /_session HTML) · server 31k (routing +
transform chain + streaming; the app).

## Feature map (flags; mechanics detail in archive-2026-06-11)

| Flag | Default | One-liner |
|---|---|---|
| `RELOCATE_ENV_TO_TAIL` / `RELOCATE_CLAUDEMD_PATHSTAMP` | on | Peel volatile `# Environment`/`# currentDate` to a tail reminder; own cache marker for static CLAUDE.md → env-independent shared segment. |
| `SORT_TOOLS` | on | Alphabetize tools[]; predictability only. |
| *(retired 2026-06-12)* `WB_INTENT_DISPATCH` | — | REMOVED (`proxylab/wb.py` deleted): proxy-side `[wb:]` intent parsing is gone; the workbench consumes the app-agnostic `SUBSCRIBERS` feed. No app-specific protocol parsing remains in the proxy. |
| `SUBSCRIBERS` (+`_ALLOW_REMOTE` 0, `_TOKEN`, `_DELTA_MS` 300, `_MAX_FAILURES` 10) | on | **App-agnostic push feed; protocol contract = `SUBSCRIBERS.md`** (the push deep-dive; the FRONT-DOOR integration contract is `INTEGRATION.md` — hand that to consumers first, it indexes the whole endpoint surface). `POST /_subscribe` {url, token, agent globs, events} → `text.delta` (normalized streaming text, offset reassembly, both wire dialects), `turn.completed` (full text + usage/cost/session-totals/context/warmth receipts — the wire-only facts: refusals, real USD, resolved model), `session.ended`. Agent-routed traffic only (`ext` never pushed); upsert by url; at-most-once fire-and-forget, suspension after N consecutive failures (re-POST reactivates); registry persists in warmth.sqlite owner-scoped; reconciliation is pull (`/_status?session=`). Loopback-only callbacks by default (exfil guard). Verified live 2026-06-12 (all 3 events, both deltas+receipts). |
| *(provider)* openai/codex routing (`UPSTREAM_OPENAI`, `CODEX_AUTH_FILE`, `CODEX_MODELS_STUB`) | on | `/agent/<name>/openai/…` → ChatGPT codex backend: strip `/v1`, OAuth headers re-read per request from `~/.codex/auth.json` (redacted in captures), models stub, SSE-by-path, zstd decoded observer-side. Server-side caching (`prompt_cache_key` = routing hint only) → NO warmth/transform stack applies. Codex 0.139+ tries a WebSocket first, 403s, falls back (~3–8s) — known codex bug; custom model_provider with `supports_websockets=false` avoids it. |
| `CANARY` (`CANARY_DIR`) | on | Read-only structural drift detector per (model, beta); appends `_canary/changes.jsonl`. Gaps #1–#3 open: system-block heading lists, control-plane keys, message roles not fingerprinted. |
| `WARMTH_LEDGER` (`WARMTH_DB`) | on | The SQLite warmth store (see verdicts above). |
| `WARMTH_BLOCK_COLD_PING` | off (script: on) | Warm-only ping gate; never higher cost. |
| `WARMTH_PINGER` (`WARMTH_PINGER_MAX`) | on | In-memory replay of a session's last request: `POST /_ping?session=<id>[&force=1]` → cache read, TTL slides, ~1 tok. Auth headers in memory only. |
| `WARMTH_HOLD` (`_MAX_HOURS` 12, `_MARGIN` 300s, `_INTERVAL` 60s, `_MAX_PINGS` 24) | on | `/warm-cache <n>` arms n hours of IDLE INSURANCE (until = last organic turn + n; every real turn re-anchors + resets counters). Echo-forward arming: proxy injects a `[logproxy]` echo block, the MODEL speaks the ack; command file is default-dead without the proxy. **Programmatic twin: `POST /_hold?session=&hours=N` (`hours=0`/`&action=off` disarms; `GET` reads state)** — same `_arm_hold` core, no forwarded turn. **Cold-gated like `/_ping`: declines to arm a non-warm prefix (200, `armed:false, skipped:<state>`; force=1 overrides), since with no forwarded turn there's nothing to keep warm.** Convention across action endpoints: HTTP status = request validity; outcome is in the body (`ok`/`armed`/`warmed`/`skipped`), 4xx only for malformed requests. Structured fields are truth (`armed`/`pingable`/`warmth_state`); the `ack` string is human prose. Disarm on expiry/`off`/`/_end`/ping cap/2 failures. |
| `WARMTH_AUTH_BOOTSTRAP` (`_MODEL`, `_MAX` 2, `_COOLDOWN` 600s) | on | A hold stuck on missing/stale auth (post-restart gap or 401'd ping) makes the proxy spawn ONE minimal haiku turn through itself to re-donate account headers. Bounded; kill switch `=0`. PROMPT BEFORE `--tools` (variadic flag eats positionals). |
| *(persistence)* restart-amnesia fix | on | Holds / last-request bodies (non-secret headers) / totals / session identity persist in warmth.sqlite scoped by `owner = LOG_DIR`; reload at startup. CREDENTIALS never persist — restored sessions are `awaiting_auth` until the account's next live request re-donates (in-memory `_ACCOUNT_AUTH`, account-level). |
| *(endpoints)* `GET /_identity`, `GET /_status[?session=][&all=1]`, `GET /_admin`, `GET /_session?session=`, `GET/POST /_ping`, `/_warm`, `GET/POST /_hold`, `/_end`, `GET/POST/DELETE /_subscribe` | — | `/_identity` = the "is this our proxy?" handshake (product/version/protocols/live-capabilities/endpoint-map + `X-Logproxy-Version` header; consumers check `product=="logproxy"` before integrating — see SUBSCRIBERS.md §Discovery). Status JSON (titles/cwd/model/warmth/hold/cost/turns/context); admin = dark HTML render, 10s refresh (sessions split into WARM-cache / COLD-expired tables — state, not recency, so a warm 1h prefix outranks a lapsed 5m one; `?show=N` caps enrichment+render to the N most-recent, default 60, with show-more/all links — bounds the per-session warmth queries on a big 24h window); /_session = HTML view of the replayable last request (turn-grouped timeline + last answer; codex sessions render view-only, never pingable). `/_end` (SessionEnd hook) = MARKER not delete: disarms hold, stamps ended_at, keeps debug state for the sweeper to reap (~ttl+grace). Bootstrap-spawned sessions tagged `kind:bootstrap`, hidden unless `?all=1`. All unauthenticated, localhost lab-grade (open item g). |
| `WARMTH_SWEEP_INTERVAL` / `WARMTH_LAST_REQUEST_GRACE` / `WARMTH_PURGE_SLACK` | 300s/600s/7d | Hygiene-only sweeper; correctness never depends on it. |
| `STRIP_COMPACT_CACHE` | off (script: on) | Warmth-gated: strip message-level cache markers on a cold `/compact`. |
| `SPLIT_SYSTEM_REST` | off | Ride static system-prose head as cache READ; wins only for trimmed-tool layouts. |
| `STRIP_SYSTEM_SECTIONS` | on (strips `# Session-specific guidance`) | Remove whole `# Heading` system sections. |
| `INJECT*` family | off | Request injection (transient, desyncs history — prefer `@file`). |
| `RESP_APPEND` / `RESP_REPLACE` | off | Mutate response text (durable). |
| `SHORTCIRCUIT_*` | off | See SC verdict above. RESP_*/relay/SC stay OFF for agent-routed (workbench) traffic. |

`analyze_tools.py` — offline tool-utilization ledger:
`python3 analyze_tools.py <dir> --by role|session`. Prices deadweight.

**Test suites: `python3 test_warmth_store.py`** (161 offline checks) — run after
any warmth/pricing/hold/persistence edit — **and `python3 test_subscribers.py`**
(52 offline checks) after any subs/tee/server-wiring edit.

## Operational state (VERIFY before continuing)

- **RELEASE MODEL (since v0.1.0, 2026-06-11): the official `:7800` proxy runs
  FROM `releases/current`** (a frozen git worktree of a tag), NOT from this
  working tree — dev edits/restarts here never disturb the agents on :7800.
  Cut: `./release.sh vX.Y.Z` (clean tree + test suite gated, tags, worktree,
  flips the `releases/current` symlink). Deploy: `./run_release.sh` (restarts
  :7800 from releases/current; LOG_DIR/WARMTH_DB/OUT pinned to the lab root so
  state+captures carry across releases; sources gitignored `release.env` —
  the home for `WB_PARSER_TOKEN` at the workbench flip).
- **Proxies launch via `./start_proxy.sh` / `./restart_proxy.sh`**
  (nohup+disown → PPID 1; a `run_in_background` Bash job dies with the CLI —
  never launch the proxy that way). start refuses a bound port; restart kills
  + starts. Script defaults add `STRIP_COMPACT_CACHE=1 WARMTH_BLOCK_COLD_PING=1
  WARMTH_LOG_FILE=1`; `${VAR-default}` so an explicit 0/empty sticks.
  Experiment arm = scratch port from the dev tree: `PORT=7802
  LOG_DIR=logs_scratch <flags> ./start_proxy.sh`.
  Sanity: `curl -s localhost:7800/_status` or `localhost:7800/_admin`.
- **`:7799` and `:8080` are the human's — leave alone** (:7799 is an old
  logproxy writing to `logs/` — don't archive/touch `logs/`).
- Restarts are safe-ish (state persists; only credentials gap, which the auth
  bootstrap closes) but avoid mid-experiment. This Claude Code session does
  NOT route through :7800 — drills must set `ANTHROPIC_BASE_URL` explicitly.
- **Client integration SHIPS with the proxy: `client/`** (warm-cache command,
  statusline, cache-expiry + cache-state hooks, settings.example.json,
  install.sh + README). Canonical copies — edit there, cut a release to ship;
  project settings reference `releases/current/client/...` so wiring upgrades
  with releases. `~/.claude/commands/warm-cache.md` is a SYMLINK through
  releases/current (install.sh). SessionEnd→`/_end` hook installed user-level
  in `~/.claude/settings.json` (pinned to :7800; scratch ports rely on the
  sweeper). `~/tmp/proxy-sl-test` was the dev sandbox for these.
- **Old experiment captures moved to `logs_archive/` (2026-06-11)** — dir
  names unchanged (logs_live, logs_chatty, logs_compact_warmth, logs_inject,
  logs_codexprobe, …; retired port→corpus map in archive-2026-06-11). Live
  dirs at root: `logs_main` (:7800) and `logs` (:7799). All gitignored.
- Git since 2026-06-09; commit after meaningful changes.

## Open / next

- (c) Deterministic transcript cleaner (Tier-1 bookkeeping strip, Tier-2
  supersession stubs; must be warmth-gated). Measure % reclaimed.
- (e) `_classify_role` mislabels headless parents (`ext/unknown`) — accept the
  "Claude Agent SDK" header (fable capture to diff:
  `logs_archive/logs_compact_warmth/7bc2d1d6-*/`).
- (g) Endpoint hardening before any shared-host use (token-gate /_ping /_end
  /_warm /_status /_admin; /_ping spends credits, bootstrap spawns turns).
- Canary gaps #1–#3: fingerprint system-block heading lists, control-plane
  keys (`thinking.type`, `output_config`, `context_management`), message
  roles. Plus: refusal counter in totals (canary can't see responses).
- A/B `--exclude-dynamic-system-prompt-sections` vs RELOCATE_ENV_TO_TAIL.
- ~~WB flip~~ DONE 2026-06-12: workbench moved to the SUBSCRIBER contract;
  `WB_INTENT_DISPATCH` + `proxylab/wb.py` removed. Residual: verify the
  workbench actually REGISTERS via /_subscribe on the deployed proxy before
  the removal release goes live (at removal time :7800 showed subscribers=0
  while legacy had dispatched 4 — likely the workbench app just wasn't up).
- Statusline TITLE display (statusline already polls /_status for
  warmth/hold/turn; script `~/tmp/proxy-sl-test/.claude/status-line.sh`).
- Measured A/B: proxied all-levers vs vanilla 1P.
- Carryovers: writer thread swallows exceptions (`except: pass` — add dropped
  counter); `_LAST_REQUEST` cap generous; `_META_CWD_TRIES`/`_ACCOUNT_AUTH`
  grow unbounded (tiny); SC priors are 4.x-only.

## fable-5 intel (2026-06-09/10; condensed — full notes in archives)

- Wire shape structurally identical to 4.x (3 sys blocks, 3 markers);
  transforms hold. New betas incl. `effort`, `mid-conversation-system`
  (SEEN LIVE: Agent-roster as trailing `role:"system"` message — uncacheable
  on debut turn, 1× once, cached next; audit user/assistant-only assumptions).
- **Control-plane keys:** `thinking:{type:adaptive}` (fable-only) is COUPLED
  to `context_management` — mutate both or neither (the pinger 400 bug).
  `output_config:{effort:high}` scales the 5×-priced output side — pin
  CLAUDE_EFFORT in every A/B arm.
- **Server-side refusal classifier** (fable endpoint only): can hard-block on
  system-prompt CONTENT (`stop_reason:"refusal"`, structured stop_details,
  zero content blocks, model never ran). Workbench-style "log every event"
  prose triggers it; non-deterministic on later turns; CLI shows a generic
  toast — the truth is wire-only. A refused turn still bills (and warms) the
  full prefix write. Workbench stays on 4.x; classifier-weather probe =
  workbench prompt + "2+2" through :7800.
- **Prompt families are MODEL-gated** (same CLI, same day): classic (~13k ch,
  sonnet/haiku) vs harness (~2-3k, opus) vs harness+`# Communicating with the
  user` (fable). Sampling trap: a session's FIRST request is the 1.2k-ch
  title side-call — filter `tools > 0` before sampling prompts.
- Mid-session `/model` switch does NOT rewrite the in-context system prompt —
  trust `resolved_model` on the wire, not self-reports.
- fable pricing: $10/$50 per MTok (2× opus-4.8); cache write 12.5/20, read 1.0.
