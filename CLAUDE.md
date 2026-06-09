# proxy-lab — handoff for the next session

## ⚡ NOTE TO SELF — state as of session end 2026-06-09 (evening, fable-5)

Big rework LANDED THIS SESSION, code-complete and tested, but NOT yet running
on the live ports:

- **SHIPPED:** warmth ledger → shared SQLite (`warmth.sqlite`, `WARMTH_DB`),
  TWO-STATE gates (warm vs not-warm; strip-on-absent is deliberate — user
  decision, see Warmth section), sweeper demoted to hygiene-only (the old one
  had a real bug: reaped cold evidence at bare ttl → compact-strip could only
  fire ≤300s past expiry), pricing fixed (fable-5 = $10/$50 = 2× opus-4.8;
  opus-4.5+ split from legacy $15/$75 — **logs_opus USD numbers were ~3× high**;
  unpriced-model accounting now loud), handler fail-open on non-JSON bodies
  (was NameError→500). All verified: `python3 test_warmth_store.py` (24 checks)
  + endpoint smoke test on :7899.
- **RESTART: DONE (2026-06-09 late evening).** All eight lab ports killed and
  relaunched via `./start_proxy.sh` — new SQLite/two-state code is live;
  `warmth.sqlite` created; `/_warm`/`/_ping` smoke-checked on :7813 (clean
  not-found on a bogus session). `_LAST_REQUEST` started empty as expected.
  **Superseded the same evening by the ONE-PROXY consolidation** (user: "one
  proxy to rule them all") — zoo decommissioned, `:7800 → logs_main` is the only
  proxy now; see Operational state.
  **DISCREPANCY FOUND during restart (`ps -E` on the old pids):** the OLD :7813
  process had NO `RELOCATE_*/SORT_TOOLS/CANARY/STRIP_SYSTEM_SECTIONS` overrides
  in its environment — i.e. it ran with those transforms at their DEFAULTS (ON),
  not "vanilla transforms OFF" as this file claimed. So pre-restart
  `logs_compact_warmth` captures (incl. the warm/cold strip verdicts and the
  fable 7bc2d1d6 capture) were taken WITH transforms on. A/B-internal
  conclusions (warm declines / cold strips) compared like-with-like and stand;
  just don't treat those captures as transform-free wire shapes. The relaunched
  :7813 NOW matches the documented config (explicit =0s, verified via ps -E).
- **REVIEW FINDINGS FIXED:** sweeper bug, NameError, stale WARMTH_BLOCK_COLD_PING
  comment, analyze_tools silent sonnet pricing, flat-cache_creation undercount.
  **FOUND BUT NOT FIXED (small):** writer thread swallows exceptions silently
  (`except: pass` — add a dropped-writes counter); `_LAST_REQUEST` cap (2000)
  is generous for full bodies in memory.
- **STRATEGIC NOTES from the fresh-eyes review (user broadly agreed):** (1) the
  two biggest savings are native (tool-trim, @file) — worth ONE measured A/B of
  "net cost of being proxied, all levers on, vs vanilla 1P" before treating the
  proxy as a deployed optimizer (user: org angle tangential, tools-for-self);
  (2) transcript cleaner (open item c) Tier-2 supersession-stubs REWRITE early
  history → bust downstream cache on a warm prefix — gate Tier-2 on the warmth
  ledger / apply at compact-time, same logic as compact-strip; (3) SC priors are
  4.x-only — revalidate on fable before using.
- **NEXT BUILD TARGET (unchanged):** the no-proxy `hold-warm` driver loop
  (poll `/_warm?session=`, fire `/_ping` near TTL edge, cap + heartbeat) — now
  on a durable two-state foundation. Then statusline (b).

A HANDOFF note, not an archive. Goal: get a fresh context productive fast.
The blow-by-blow record (every experiment, session-id, intermediate
correction) lives in `CLAUDE.md.archive-2026-06-09` — read it only if you need
to re-derive *why* a conclusion holds. This file = the conclusions + how to run.

## What this is

`logproxy.py` = a transparent analytical forward-proxy between the `claude` CLI
and `api.anthropic.com`. Point the CLI at it via `ANTHROPIC_BASE_URL`. It
forwards bytes verbatim and **captures** every request/response to study agentic
traffic. **Mission: find & price context waste; stay non-intrusive (no visible
overhead).** All disk I/O runs on a background writer thread + queue; the handler
only parses + enqueues.

Driving experiments live in a sibling project:
`/Users/bogdan/projects/proxy-experiments/` (own CLAUDE.md). proxy-lab = the
mechanism; proxy-experiments = scripts that drive `claude -p` through a proxy
port and analyze captures. Only coupling: the proxy port + `LOG_DIR`.

## The thesis (proven on the wire)

**Cost is context carriage, not "the model thinking."** Over an 18-turn trivial
session: carriage (input+cache_read+cache_write) = $2.72; actual output (950
tok) = $0.07 — carriage is 38× the cost. `cache_read` = the same ~38k context
re-shipped every turn.
- **"Cached so it's cheap" is backwards.** The right baseline for junk context is
  *zero*. Cache bills 10% of something that should be 0, every turn. Cached
  content is still fully in the payload (bandwidth + context-window occupancy are
  NOT discounted — only compute price is).
- A "2+2" turn loaded 31 tools (~23k tok), used 0 — deadweight, re-read every turn.

## Biggest practical lever: TOOL-SET TRIMMING (native, no proxy)

Default `claude` ships **~33 tools ≈ 24k tok EVERY turn**; a normal session uses
~4. `Workflow` tool alone = 5,223 tok (disable via `CLAUDE_CODE_DISABLE_WORKFLOWS=1`);
the Team/Task/Cron/etc orchestration tools ~15k more. **Fix:**
`claude --tools "Read Edit Write Bash Glob Grep"` (trims tools[] on the wire —
unlike `--allowedTools` which only gates permissions) + `CLAUDE_CODE_DISABLE_WORKFLOWS=1`.
Measured chatty 14-msg dev session: **−72.7% tokens, −50% USD, same work.**

## Native answers that beat the proxy (use these first)

- **`@file`** — the CLI inlines it AND writes `readFileState`, so `@sample.py`
  → 0 Reads, Edit runs directly, −33% tokens, fully safe. Most injection tricks
  were reinventing `@`. (Volunteering just a PATH without `@` does NOT help.)
- Use native `@`/`!`/`/`/hooks for context & read-collapse. Reserve the PROXY for
  what only a wire intermediary can do: turn-collapse (request piggyback) and
  DURABLE response mutation.

## Hard facts (don't re-derive)

**Cache mechanics**
- Caching is **prefix-based, content-addressed, byte-exact**. Tail edits (last
  user msg) are cache-safe; system/tools/history edits bust downstream.
- Canonical cache order is **tools → system → messages** regardless of JSON key
  order. ≤4 `cache_control` breakpoints; each = "cache the cumulative prefix up
  to and including this block." Typical layout: M1 = tools+attribution+preamble
  (SEGMENT A), M2 = system prose + `# Environment` (SEGMENT B), M3 = messages
  (rolling, SEGMENT C).
- **CLAUDE.md is USER space**, not system — a `<system-reminder>`/`# claudeMd`
  text block in `messages[0]`. cwd's project CLAUDE.md auto-loads → run clean
  experiments from a neutral cwd.
- **TTL: 5m is the default; 1h is an override the CLI adds ONLY to the main
  agent** (subagents stay bare = 5m). `FORCE_PROMPT_CACHING_5M=1` works by
  *dropping* the 1h field. TTL is a **sliding idle timer** (resets on every read),
  not fixed-from-creation. Write premium: 5m = 1.25×, 1h = 2×; reads = 0.10× in
  both. **5m-vs-1h is purely the reuse-gap:** gap <5m → 5m; 5m–1h → 1h; >1h →
  neither/no-cache. **Rule: ephemeral one-shot agents → 5m, persistent brains →
  1h; default to 5m when unsure** (mis-1h on the ephemeral majority wastes +12–22%).
- **Mid-turn eviction is real** — a tool step longer than the TTL between dispatch
  and continuation of one turn re-writes the prefix. 1h's real value = shielding a
  slow in-flight tool/approval-pause.
- **Org/proxy scope caveat:** a custom `ANTHROPIC_BASE_URL` (our proxy) flips the
  client to 3P → org cache scope → the static/dynamic system boundary is never
  inserted → static+dynamic system are bundled. So no system cache survives an env
  change (cwd/git-commit/CLAUDE.md edit all bust `rest`). Everything we measure is
  this degraded org path. The `# Environment` block carries cwd/dirs/platform/model
  — NOT git branch/commits in this CLI; the real buster is CWD.

**The read-before-edit gate (SKIP-THE-READ, resolved)**
- `FileEditTool.validateInput` checks client-side `readFileState.get(path)` BEFORE
  old_string — empty ⇒ errorCode 6 "File has not been read yet." `readFileState`
  is written in 6 LOCAL spots (Read/Write/Edit/NotebookEdit/Bash/attachments),
  **none on the API wire** → a transparent proxy CANNOT forge it live.
  Proxy-forged Read-pair is a predictable NULL; don't run it.
- The mutation Read IS collapsible by **tool substitution** (the gate is
  FileEditTool-specific): Bash/Write have no read-precondition, or a custom
  read-internally MCP edit tool (keeps integrity). But `@file` is the clean native
  win — use it.

**Response mutation persists**
- The CLI accepts altered text responses with ZERO pushback (no signature check on
  text — never touch THINKING blocks, they carry signatures). Response edits
  PERSIST into the transcript (durable conversation state), unlike request
  injection (transient, desyncs history).

**/compact**
- NOT a text `-p` command — send via `--input-format stream-json --output-format
  stream-json`, one line `{"type":"user","message":{"role":"user","content":"/compact"}}`.
  Emits a `system/compact_boundary` event (`pre_tokens`/`post_tokens`) and persists;
  `--resume`/`--fork-session` continue from the SUMMARY. Disable auto-compact with
  `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=100`.

## Code feature map (logproxy.py)

Core (always on): per-session log dirs `LOG_DIR/<sid>/<seq>-<agent>-<role>-<model>-<HHMMSS>.{request,response}.json(+.sse)`;
`_totals.json`/`_session.json`; off-hot-path writer thread; session_id parsing
(nested in `metadata.user_id`); inbound transport + redacted headers capture.

| Flag | Default | What it does |
|---|---|---|
| `RELOCATE_ENV_TO_TAIL` / `RELOCATE_CLAUDEMD_PATHSTAMP` | **on** | Peel volatile `# Environment`/`# currentDate` down to a tail reminder before the prompt; give static CLAUDE.md its own cache marker; dedupe the abspath stamp → CLAUDE.md becomes an env-independent, cross-session-shared segment (−94% claudeMd write on a cwd change). Marker TTL mirrors the preceding system marker (the 5m-after-1h 400 bug is fixed). Falls back to the userEmail bundle when no CLAUDE.md. |
| `SORT_TOOLS` | **on** | Alphabetize `body.tools`; idempotent. Predictability only, not a carriage win. |
| `CANARY` (`CANARY_DIR`) | **on** | Read-only version-drift detector: structural fingerprint per (model, anthropic-beta) — tool count/names, system block shapes, cache_control marker counts. Fires on CLI/wire changes (not our transforms; message content excluded). Early-warning for every version-fragile lever; flags when the CLI ever ships a 4th cache marker (baseline=3). Appends `_canary/changes.jsonl`. |
| `WARMTH_LEDGER` (+ `WARMTH_LOG_FILE`, `WARMTH_PING_SENTINEL`, `WARMTH_DB`) | **on** | See "Warmth ledger" below. **SQLite-backed since 2026-06-09**: `warmth.sqlite` next to logproxy.py (override `WARMTH_DB`), WAL mode, shared by ALL proxy ports, survives restarts. `WARMTH_LEDGER_MAX` is gone (expiry is the GC). |
| `WARMTH_BLOCK_COLD_PING` | off | **Warm-only ping gate.** A ping pays off ONLY on a `warm` prefix (a 0.10× read that slides the TTL); on any non-warm state (`cold`/`absent`/store error) a replay is a cache WRITE at the premium for no gain. So **ping IFF warm; decline everything else** (sentinel path short-circuits with a synthetic `end_turn`, 0 tokens). The replay pinger (`/_ping`) enforces warm-only unconditionally; `force=1` is the only override. Principle: **never higher cost.** (The "native-TTL store's EXISTS is the perfect primitive" insight is now implemented — the SQLite expiry predicate IS the gate.) |
| `WARMTH_PINGER` (+ `WARMTH_PINGER_MAX`) | **on** | Replay-pinger: caches each session's post-transform last messages request **in memory** (incl. auth/beta headers, never on disk; oldest-evicted past cap). `POST`/`GET /_ping?session=<id>[&force=1]` replays it with thinking off + `max_tokens:1` → a cache READ that slides the TTL, ~1 output tok. Collapses the old `--resume --fork-session` sentinel dance to one HTTP call with just the session_id. Skips any non-warm prefix (warm-only; force=1 to establish). |
| `WARMTH_SWEEP_INTERVAL` / `WARMTH_LAST_REQUEST_GRACE` / `WARMTH_PURGE_SLACK` | 300s / 600s / 7d | **Housekeeping-only** sweeper (runs when pinger OR ledger on): drops in-memory cached last-requests whose prefix lapsed past the grace (judged by the ledger's last-touch, so an actively-pinged session survives), purges warmth rows expired longer than the slack, prunes stale heads. Correctness lives in the READ PREDICATE (`expires_at > now`), never in these deletions — the sweep can run late or never without changing any gate decision. |
| `STRIP_COMPACT_CACHE` (`STRIP_COMPACT_FORCE`) | off | Drop MESSAGE-level cache_control on a `/compact` request (keep system markers) when history is **not warm** — reclaims the discarded write premium. TWO-STATE gate (2026-06-09): `warm` declines; `cold`/`absent` strip; ledger-off/store-error decline. |
| `SPLIT_SYSTEM_REST` (`SPLIT_SYSTEM_REST_MARKER`) | off | Move the static system-prose head onto the preceding marked block so it rides as cache_READ not WRITE. Byte-identical text. Win is ~60% of system-prefix carriage **only** for trimmed-tool layouts (which get no global fleet warming anyway, so the split forfeits nothing); for full-tool layouts vanilla's free global warming wins. |
| `STRIP_SYSTEM_SECTIONS` | on (strips `# Session-specific guidance`) | Remove whole `# Heading` system sections. Marginal carriage; real use = system-prompt-edit mechanism. Set empty if a session needs that section. |
| `INJECT*` family | off | Request injection: unconditional / marker-gated / `INJECT_FILE` volunteer-context (+`INJECT_FILE_NOTE`). |
| `RESP_APPEND` / `RESP_REPLACE` | off | Mutate the response text (buffers SSE, re-emits once). |
| `SHORTCIRCUIT_*` | off | Elide the post-edit "Done." wrap-up turn. See below. |

`analyze_tools.py` — offline tool-utilization ledger (keys off file CONTENT):
`python3 analyze_tools.py <dir> --by role|session [--min-turns N]`. Loaded-vs-called
per tool, prices deadweight.

## Warmth ledger (SQLite, TWO-STATE) + warmth-gated compact-strip (redesigned 2026-06-09)

The bust-detector for `STRIP_COMPACT_CACHE` and the ping gate. A forked
keep-warm ping refreshes the ORIGINAL session's content-addressed prefix cache,
but never lands in the original's JSONL — so **warmth lives on the PREFIX
LINEAGE, not the session_id**. The proxy can't know warmth from a request alone,
so it LEARNS it from responses and stores it.

- **STORE: shared SQLite** (`warmth.sqlite` next to logproxy.py; `WARMTH_DB`
  overrides), WAL mode. Durable across proxy restarts; ONE ledger shared by all
  proxy ports (was: eight blind in-process dicts). Chosen over Redis: stdlib, no
  daemon, durable per-commit by default, and no "store unreachable" runtime state
  to mishandle now that absence triggers action. Only anonymous prefix hashes +
  timestamps touch disk; `_LAST_REQUEST` (bodies + auth) stays in-process.
- **TWO-STATE (the 2026-06-09 decision; replaces warm/cold/unknown):** the expiry
  predicate IS the answer — warm = row exists AND `expires_at > now`; everything
  else is not-warm. Durable + receipt-confirmed ⇒ **absence ≈ expiry**, so the
  compact gate may act on absence; the residual loss case (absent-but-warm:
  pre-store sessions, bypassed traffic) is one bounded ~0.9×H overpay on a
  one-shot compact. Gates: **ping IFF warm**; **strip IFF not-warm**
  (`cold`/`absent`); ledger-off/store-error DECLINE on both (can't judge ≠
  evidence). `warmth_state` still reports `cold` (lapsed row awaiting purge) vs
  `absent` for logs only — no gate distinguishes them.
- **EXPIRY IS THE GC:** correctness lives in the read predicate, never in row
  deletion; the sweeper is hygiene-only (purge slack `WARMTH_PURGE_SLACK`=7d).
  This fixed a REAL BUG the redesign surfaced: the old in-memory sweeper reaped
  lapsed entries at bare ttl, erasing the cold evidence the three-state gate
  required — the strip could only fire within ≤1 sweep interval (300s) of expiry,
  so the canonical come-back-after-an-hour-and-compact case read `unknown` and
  silently declined. (The 360s-idle verification run passed only because it sat
  inside that window.)
- **WRITE side** (`_record_warmth`): hashes the just-cached POST-transform prefix
  (blake2b chain over `_stable_sys_text` + messages, cache_control stripped) and
  upserts `(hash, now, ttl, now+ttl)`. **RESPONSE-CONFIRMED stamp** unchanged:
  stamps IFF usage proves caching happened (`cache_creation>0` or `cache_read>0`);
  both-zero is NOT stamped — request is intent, response is the receipt (this
  discipline is what makes absence≈expiry honest). Failed stamps print LOUDLY
  (a silent miss would degrade warm→absent, which now strips).
- **Don't regress:** (1) **depth-scan** — the reused breakpoint sits several
  messages back from the tail; `_compact_history_warmth` checks every cumulative
  prefix below the compact prompt (one batched `IN` query); any warm depth ⇒
  decline. (2) **Exclude the attribution block** (`sys[0]` billing-header with the
  per-turn `cch=N` counter, out-of-band, does NOT cache) — `_stable_sys_text`.
- **Verdicts:** live (haiku, 5m): WARM arm declined strip → cache_read=7086;
  COLD arm (idle 360s) stripped → input=23748, cache_read=0. Plus
  **`test_warmth_store.py`** (24 offline checks: warm-declines / cold-strips /
  absent-strips / purge-invariance / off-declines / receipt discipline /
  restart-durability / session-head + `/_end` / pricing). Run it after any
  warmth or pricing edit: `python3 test_warmth_store.py`.

## Compact-on-busted-cache economics (verdict, 2026-06-07)

On a busted cache, compacting the **conversation layer** (tools/system are
identical in both arms and must be subtracted) beats continuing when the summary
is small. Rule: **compact wins the first turn iff `read + 5S < premium·H`** →
S < 0.20·H at 1h, S < 0.05·H at 5m (H=history, S=summary). Typical compaction is
5–15% → wins ~immediately at 1h. The summary's OUTPUT cost (5× input) is the only
thing keeping it off op1; at 1h it's covered by the 2× write premium that
continuing pays. `STRIP_COMPACT_CACHE` is the lever that flips 1h from a liability
to an asset (the stripped compact reads history 1×; continuing cold-rewrites at
2×). On a WARM cache, stripping is a big LOSS (read 0.10× → input 1.0×) — hence
the warmth gate.

## SHORTCIRCUIT (SC) — elide the terminal-edit wrap-up turn

A `tool_use` response always gets `stop_reason:"tool_use"`, so even a successful
terminal edit costs a whole turn to say "Done." SC supplies the missing "last
action" affordance: when a tool_result continuation is for a SUCCESSFUL terminal
tool whose dispatch message carried a sentinel in TEXT, the proxy returns a
synthetic end_turn WITHOUT calling upstream. Config: `SHORTCIRCUIT_DONE=<sc_done>`
+ `SHORTCIRCUIT_SYSPATCH=1` (best delivery). `SHORTCIRCUIT_ACK` = canned reply.
- **Use canned-ack, NOT relay** — relay strips the dispatch text block, the CLI
  rejects the synthetic end_turn and re-sends → 0 net savings on builds.
- **Delivery axis (controlled A/B):** the model obeys a same-message
  output-composition directive in a CONVERSATION channel (system OR user msg) but
  IGNORES one in a tool DESCRIPTION. Winner = **SYSPATCH** (system prompt:
  cache-rides, invisible, fires 3/3 on sonnet). toolpatch = negative control (0/3).
- **Prompt strength is the build lever:** BLUNT PROHIBITION + worked exemplar
  ("the SAME message MUST also contain a past-tense summary ending `<sc_done>`;
  never send a lone tool call; never defer; no forward narration; assume success")
  → sonnet trivial 3/3 AND build 3/3, **−49.5% tokens**. GENTLE/temporal/"if it
  fails react" phrasing → 0/3 (legitimizes deferral).
- **Model-specific:** only **sonnet** reliably front-loads under "assume success."
  **opus and haiku DEFER** with adaptive thinking on (emit `[thinking, bare
  tool_use]`, summarize on the wrap-up turn). Likely lever = thinking mode
  (untested thinking-off). A miss is FREE (normal wrap-up, no corruption). SC is a
  high-end optimization — don't bother on haiku.

## Operational state (VERIFY before continuing)

- **Always launch proxies via `./start_proxy.sh`** (nohup+disown → PPID 1,
  survives /clear + CLI exit). A Claude Code `run_in_background` Bash job is a
  CHILD of the CLI and gets reaped on exit — never launch the proxy that way.
  Kill a port: `kill $(lsof -nP -tiTCP:<port> -sTCP:LISTEN)`.
- **ONE PROXY (consolidated 2026-06-09, late evening — user decision):** the
  organic 8-port zoo (7800–7803, 7810–7813) is DECOMMISSIONED. Bare
  `./start_proxy.sh` = THE proxy: **`:7800` → `logs_main`**, all levers on —
  code defaults (RELOCATE_*, SORT_TOOLS, CANARY, STRIP_SYSTEM_SECTIONS,
  WARMTH_LEDGER, WARMTH_PINGER) + script defaults (`STRIP_COMPACT_CACHE=1
  WARMTH_BLOCK_COLD_PING=1 WARMTH_LOG_FILE=1`). Features are env vars; an
  experiment arm is just an override on a scratch port:
  `PORT=7802 LOG_DIR=logs_scratch <flags> ./start_proxy.sh`. The script uses
  `${VAR-default}` so an explicit `=0`/empty from the caller sticks.
  - This config has all transforms ON. For a transform-free warmth arm
    (the retired `:7813` config): `STRIP_COMPACT_CACHE=1` + warmth stack, but
    `RELOCATE_ENV_TO_TAIL=0 RELOCATE_CLAUDEMD_PATHSTAMP=0 SORT_TOOLS=0 CANARY=0
    STRIP_SYSTEM_SECTIONS=`.
  - Retired port→corpus map (for provenance; dirs kept): 7800→logs_live (clean
    observer), 7801→logs_inject (INJECT_MARKER=Math:), 7802→logs_chatty,
    7803→logs_inject (SC: SYSPATCH+DONE), 7810→logs_opus (defaults),
    7811→logs_compact_strip, 7812→logs_forkcache, 7813→logs_compact_warmth.
  - Restart caveat: restarting wipes in-memory `_LAST_REQUEST` (pings 404 until
    each session's next real turn); the SQLite ledger survives restarts. Don't
    restart mid-experiment.
  - (`:7799` and `8080` are the human's — leave alone.)
- **Git: repo initialized 2026-06-09** (first commit = post-rework code).
  `.gitignore` excludes `logs*/`, `*.out`, `warmth.sqlite*`, `_canary/`. Commit
  after meaningful changes — the lab finally has undo.
- **Key captures:** `logs_live/` (38× carriage data + subagent spawns) ·
  `logs_chatty/` (tool-trim, fat 4bcf519f / lean a0f0c609) · `logs_compact_warmth/`
  (warm fork cdcd1b7e / cold f5c27104) · split/reloc A/B pairs (`logs_split5m_on|off`,
  `logs_reloc2_on|off`).

## Open / next

- (a) **DONE (2026-06-09): proxy-side replay pinger** (`WARMTH_PINGER`, on). The
  old plan ("wire `WARMTH_PING_SENTINEL` to a fork-ping") was overkill — a fork
  has to reconstruct tools/cwd/system/history just to smuggle a sentinel turn the
  proxy then recognizes. Replaced by: the proxy caches each session's exact
  post-transform last request in memory; `POST /_ping?session=<id>` replays it
  (thinking off, `max_tokens:1`) → cache read, TTL slides, ~1 output tok. The
  caller needs only the session_id. Non-warm prefixes (cold/unknown) self-skip — ping only refreshes warm (force=1 to override).
  **Still TODO:** the outer driver that decides WHEN to ping — a no-proxy
  `hold-warm` loop polling `/_warm?session=` and firing `/_ping?session=` when
  `remaining_s` drops low (~55min at 1h), with a hard cap + heartbeat for the
  statusline. **Ping economics:** at 1h TTL one ping ≈ one warm read buys a full
  hour ≈ 19:1 → a 1h-main-agent move; CAP the count. At 5m it's a bad bet (~12
  pings/hour). The sentinel path (`WARMTH_PING_SENTINEL` + `_is_warm_ping`) is now
  legacy/optional — kept only because `WARMTH_BLOCK_COLD_PING` and `_record_warmth`
  still recognize it; the replay pinger is the recommended route.
  **Session teardown (2026-06-09):** `GET/POST /_end?session=<id>[&reason=]` forgets
  a session's cached request — wire to the CLI **SessionEnd hook** (`reason` =
  clear/logout/prompt_input_exit/other; hook gets `session_id`+`reason` on stdin).
  Hook is precise but misses crashes/`kill -9` → the background sweeper is the
  backstop. All in-memory soft-state: over-eager cleanup only costs a re-cache.
  **Verified live (2026-06-09, `logs_endexp` + `/tmp/clearexp`):**
  - **Hook session_id == wire session_id** (the open caveat — RESOLVED). SessionEnd's
    `session_id` matched the proxy's `metadata.user_id` session; `/_end` dropped the
    cached entry (`last_request:true, session_head:true`), and a later `/_ping`/`/_warm`
    for it correctly returns not-found.
  - **`/clear` DOES emit `SessionEnd reason:"clear"` mid-session AND rotates the
    session_id** (turns before ran under sid-A, turns after under a fresh sid-B). The
    hook fired while the process was still alive and `/_end` dropped sid-A. NOTE: a
    first, BROKEN run had concluded the opposite ("/clear fires nothing, one session,
    reason other") — that was an ARTIFACT of piping all stdin then immediate EOF, so
    `claude -p` drained the buffer and exited before `/clear` could act mid-stream.
    **To drive a live multi-turn stream-json session you MUST hold stdin open** (FIFO
    + a held write fd; send turns with pauses; close the fd only to end). Lesson:
    don't trust a streaming-CLI result from a here-string/`printf | claude` — it EOFs.
  - **EOF alone does NOT terminate headless `claude -p` stream-json** — after closing
    the write fd, our claude stayed alive (deadlocked our `wait`); it took a `kill`,
    which then fired the 2nd SessionEnd (`reason:"other"`) for sid-B. So a long-lived
    stream-json driver needs an explicit shutdown signal, not just stdin close.
  - **Replay pinger works on the real wire:** `/_ping` returned `cache_read=5681,
    output=1`, `remaining_s` reset to 3600. **BUGFIX found by the live test:** the
    CLI request carries `context_management: clear_thinking_*`, which 400s if you
    strip `thinking` ("requires thinking enabled") — so `_warm_session` now drops
    BOTH `thinking` and `context_management` (neither is in the cached prefix).
    Note a tiny `cache_creation` (~760 tok) per ping: the cached tail message sits
    just past the last breakpoint so it re-writes incrementally — bulk still reads.
- (b) Statusline snippet rendering `/_warm` (🔥 warm·58m vs ❄️).
- (c) Deterministic transcript CLEANER (Tier-1 lossless bookkeeping-noise strip,
  Tier-2 supersession: stale Read before a later Edit/Write → stub) before any AI
  compaction. Stubs must stay non-empty + keep tool_use/tool_result paired.
  Measure % reclaimed on a heavy edit session.
- (d) **DONE (2026-06-09): external warmth store.** SQLite chosen over Redis
  (stdlib/no daemon; durable per-commit by default vs RDB/AOF config care;
  no "store unreachable" state to mishandle once absence triggers action) —
  see the Warmth ledger section. Two-state superseded the `ttl+grace`
  positive-bust carve-out: with a durable receipt-stamped store, absence≈expiry
  and the compact gate acts on it directly. `_LAST_REQUEST` (bodies+auth)
  stayed in-process as specified.
- (e) Fix `_classify_role` mislabeling opus parents as `unknown` (cosmetic — opus's
  headless system prompt lacks the "Claude Code" signature). **fable-5 has the same
  issue** (its 4-tool parent turn logged `ext/unknown`) — capture to diff:
  `logs_compact_warmth/7bc2d1d6-*/`.
- (f) **DONE (2026-06-09): pricing blindness fixed.** Totals now carry
  `unpriced_requests`/`unpriced_models`, `[dump]` flags `UNPRICED` traffic and a
  once-per-model warning fires; `analyze_tools.py` no longer silently prices
  unknown models at sonnet rates (flags the assumption instead). **fable-5 rates
  added: $10/$50 per MTok = exactly 2× opus-4.8** (cache: write 12.5/20, read 1.0).
  Fixing this surfaced TWO latent pricing bugs, both fixed: (1) `_price_for`
  claimed longest-prefix matching but returned first-dict-hit; (2) `PRICES` had
  ONE `claude-opus-4` entry at $15/$75 — that's 4.0/4.1 pricing; **opus repriced
  at 4.5 to $5/$25**, so all opus-4.5+ captures (e.g. `logs_opus`) were priced
  ~3× too high — re-derive any USD conclusion drawn from them (token counts are
  unaffected). Also: `_billing` now prices a flat `cache_creation_input_tokens`
  (at the 5m rate, flagged in `price_basis`) instead of silently dropping write
  cost when the 5m/1h split is absent.
- (g) Endpoint hardening (lab-grade today): `/_ping` `/_end` `/_warm` are
  unauthenticated on localhost. `/_ping` SPENDS user credits (replays with cached
  auth headers), `force=1` can even cold-write big prefixes repeatedly; `/_end`
  lets any local proc drop state. Fine for a private lab box; gate with a token
  before any shared-host use. Related known gaps: cached auth headers can go STALE
  (OAuth expiry) → ping 401s, no refresh; `_cache_last_request` stores at REQUEST
  time, so a failed turn leaves an unconfirmed last-request whose hash the ledger
  never stamped → pings decline (`unknown`) until the next good turn (fail-safe but
  loses pingability); ping TOCTOU (warm check vs arrival) — drivers should ping
  with margin, never at the TTL edge.

## Model-switch day (2026-06-09): claude-fable-5 observations

User switched the CLI default to **`claude-fable-5`** (`/model`). Verified ON THE
WIRE (captures: `logs_compact_warmth/7bc2d1d6-*/`):
- The model is REAL upstream: CLI sends `claude-fable-5[1m]` (same `[1m]` 1h-cache
  suffix convention), backend `resolved_model: claude-fable-5`, answers correct.
- **CANARY VALIDATED LIVE:** first fable traffic fired `new namespace baseline`
  for both namespaces (bare + claude-code beta) exactly as designed — this is the
  drift detector doing its job on a brand-new model id.
- **Wire shape is structurally IDENTICAL to haiku-4-5:** 3 sys blocks (same headers
  + size buckets: billing-header / "You are a Claude agent…" / interactive-agent
  block), 4 tools, 3 cache markers, no 4th marker. So the version-fragile
  transforms (RELOCATE_*/STRIP_SYSTEM_SECTIONS/SPLIT) most likely still apply — but
  A/B-verify before trusting numbers on fable.
- **NEW beta flags** vs haiku namespaces: `effort-2025-11-24`,
  `mid-conversation-system-2026-04-07`, `fallback-credit-2026-06-01`,
  `context-1m-2025-08-07`. `mid-conversation-system` smells relevant to our
  system-edit levers — investigate.
- Known immediate impacts: pricing blind (open item f), role classifier `unknown`
  (open item e). Model-specific priors (SC defer behavior, tool-trim token counts,
  prompt-strength results) were measured on 4.x — re-validate on fable before
  relying on them.
- **Identity caveat for sessions**: a mid-session `/model` switch does NOT rewrite
  the already-in-context system prompt — a session started under model X keeps
  claiming to be X. Self-reports are context text, not weight introspection; trust
  the wire (`resolved_model`), not the label.

### fable-probe (2026-06-09, first experiment on THE consolidated proxy)

Scenario `fable-probe` in proxy-experiments (`--session <uuid>` re-verdicts free).
Capture: `logs_main/b3618fc5-*`. Procedure: seed `logs_main/_canary` from the old
`logs_compact_warmth/_canary` baselines (canary state is per-LOG_DIR and
lazy-loads on first messages request — seed BEFORE first traffic), then one
headless 4-tool fable-5 Write task through `:7800`. Verdicts:
- **No new prompt for fable.** Headless main agent = same 3-block structure as
  the seeded interactive baseline and as haiku: billing-header / "You are a
  Claude agent, built on Anthropic's Claude Agent SDK." (62 ch) / interactive-
  agent block (~6.7k ch), same 3-marker layout, 1h ttl on the headless main
  agent too. Only deltas (offline fp diff): headless beta set DROPS
  `context-1m-2025-08-07`, and the billing-header build stamp differs
  (interactive `cc_version=2.1.170.3` vs headless `2.1.170.ba7`).
- **Detector works end-to-end:** new (model|beta) namespaces fired `baseline`
  exactly once, the follow-up request fired `match`; the offline
  `_fp_diff_offline` (in experiments.py) pinpointed the deltas above.
- **Transforms HOLD on fable headless:** `env_relocate` fired (`# Environment` +
  `# currentDate` → tail reminder, userEmail-bundle fallback path — scratch cwd
  has no CLAUDE.md), `system_strip` removed `# Session-specific guidance`
  (528 ch), tool_sort idempotent no-op (already alphabetical). 3 markers, no 4th.
- **NEW: per-session TITLE-GENERATOR side-call** (seq 2 of the session): 0 tools,
  0 markers, own namespace (beta adds `structured-outputs-2025-12-15`), system =
  "Generate a concise, sentence-case title (3-7 words)…", `cc_entrypoint=sdk-cli`.
  Tiny but it's real traffic through the proxy — remember it when counting turns.
- **Open item (e) root cause confirmed:** headless sys[1] says "Claude Agent
  SDK", not "Claude Code" — that's the exact header `_classify_role` should also
  accept to stop logging headless parents as `ext/unknown`.
