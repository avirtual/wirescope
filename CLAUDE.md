# proxy-lab — handoff for the next session

## ⚡ Ephemeral handoff state → `HANDOFF.local.md` (gitignored)

@HANDOFF.local.md

The session-end NOTE TO SELF (what shipped, what's live, the NEXT build
target, pending carryovers) lives in **`HANDOFF.local.md`** — local-only,
gitignored, imported above so every fresh context still loads it. **Update
THAT file at session end, not this one** — future plans and in-flight state
must not leak into git commits. CLAUDE.md is for DURABLE conclusions + how to
run; commit it only when those change. If `HANDOFF.local.md` is missing
(fresh clone), recreate it from this convention — nothing durable is lost.

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
- **`--system-prompt-file` / `--system-prompt`** — natively REPLACES the whole
  Anthropic agent-prompt block (verified on the wire, sysprompt-probe
  2026-06-09): the ~11.7k-ch block becomes your text (own 1h cache marker);
  CLI still prepends the 85-ch billing header (uncached) + the 62-ch "You are
  a Claude agent, built on Anthropic's Claude Agent SDK." preamble; msg0
  context bundle (CLAUDE.md/userEmail/env) still ships. This outclasses
  STRIP_SYSTEM_SECTIONS for headless drivers — whole-prompt control, no proxy.
- **`--exclude-dynamic-system-prompt-sections`** — the CLI's NATIVE version of
  our `RELOCATE_ENV_TO_TAIL` ("move per-machine sections from the system prompt
  into the first user message; improves cross-user prompt-cache reuse"; ignored
  with `--system-prompt`). Found 2026-06-09 in `--help`; NOT yet A/B'd against
  our transform — do that before keeping ours on.
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
| `WARMTH_HOLD` (+ `_MAX_HOURS`=12, `_MARGIN`=300s, `_INTERVAL`=60s, `_MAX_PINGS`=24) | **on** | **The hold driver (2026-06-10)** — decides WHEN to ping. User arms a session in-band via `/warm-cache <n>` (`~/.claude/commands/warm-cache.md` → `<proxy:warm-cache hours=N>` sentinel): the proxy captures that turn (never forwarded, 0 tokens; the previous real turn stays the replayable prefix), arms **n hours of IDLE INSURANCE** — `until` re-anchors to last-organic-turn + n on EVERY real turn (2026-06-10 fix: "keep me warm until n hours after I walk away", NOT n hours from the arming timestamp; pings/failures reset with each slide; `hours` persisted in hold_state, legacy rows derive it from until−armed_at), answers with a synthetic end_turn ack reporting real warmth. Asyncio task pings armed sessions whose WARM prefix has `< margin` left (`_warm_session` warm-only gate is the final arbiter). Not-warm SKIPS (a real turn re-warms → hold resumes); disarm on expiry / `off` / `/_end` / ping cap / 2 consecutive failures. Spends nothing until armed. Requires PINGER+LEDGER. **Anti-ambush (2026-06-10):** the sentinel+ack pair PERSISTS in the transcript; an unattributed ack made the NEXT turn's model read the command's tripwire in history and 'retract' a real hold (observed live). Fix: acks are `[logproxy]`-prefixed and the command's tripwire is turn-scoped ("final user message of the request you are answering" fires; "earlier history" is declared inert) — true in both reading contexts, still detects a dead proxy. |
| *(endpoint)* `GET /_status[?session=][&all=1]` | — | Read-only session inventory: title (harvested from the CLI's title side-call; structured-outputs JSON unwrapped), cwd ("Primary working directory:" line), model, warmth, hold state, per-session cost + refusals + `pingable`/`awaiting_auth`, proxy flags/totals + `totals_since_start` + `restored_at_start`. Identity rows are DURABLE (`session_meta`); holds/last-request-bodies/totals are persisted too since 2026-06-10 (see restart-amnesia below). Default window: sessions seen <24h. |
| *(endpoint)* `GET /_admin[?session=][&all=1]` | — | The `/_status` snapshot rendered as HTML for humans (the "proxy admin page"): dark single-page table — warmth (🔥 + time left / ❄️ / ∅), title+sid+cwd+model, hold (until HH:MM, pings n/expected — the WHOLE hold restarts at the user's last organic turn: a real turn re-warms for free, so `until` slides to turn + armed hours, the counter resets, and expected = armed window ÷ ttl; the 24 cap is only the safety bound and is likewise per idle stretch), pingable/awaiting-auth, per-session cost, refusals; header has flags, totals and a since-restart delta. Server-rendered, zero JS, `html.escape` on everything (titles are model output), meta-refresh 10s. Same lab-grade auth posture as the other endpoints (none — localhost). |
| `WARMTH_AUTH_BOOTSTRAP` (+ `_MODEL`, `_MAX`=2, `_COOLDOWN`=600s) | **on** | **Auth self-bootstrap (user idea 2026-06-10):** when a hold tick finds an ARMED hold stuck on the post-restart auth gap (`restored without credentials`), the proxy closes the gap ITSELF — it spawns one minimal trimmed-tools haiku turn (`claude -p --tools Bash`, cwd /tmp) pointed at its own port (start_proxy.sh exports PORT); the turn flows through the normal handler and re-donates the account's headers. Credentials still never touch the proxy's disk — the spawned CLI reads them from where the CLI always keeps them. Spends real (tiny, ~$0.003) credits autonomously ⇒ tightly bounded: only for a hold that needs it, max 2 attempts/process, 10-min cooldown, one in flight, kill switch `WARMTH_AUTH_BOOTSTRAP=0`. |
| *(persistence)* restart-amnesia fix | **on** (not a flag) | **Open item (h), SHIPPED 2026-06-10.** Per-proxy runtime state is mirrored to warmth.sqlite and reloaded at startup, scoped by `owner = LOG_DIR` (a scratch port never resurrects the main proxy's sessions): `_HOLD_STATE` → `hold_state` table (mirrored on arm/disarm/every ping; expired rows reaped at load); `_LAST_REQUEST` → `last_request` table (post-transform body + NON-SECRET headers on the writer thread; **auth headers never touch disk** — restored entries are `needs_auth` and pings DECLINE cleanly (`skipped:no_auth`, no failure count, no hold ping-slot burned) until the account's first live request re-donates credentials via the in-memory `_ACCOUNT_AUTH` registry (auth is account-level, not session-level)); `_TOTALS`/`_SESSION_TOTALS` reloaded from the `_totals.json`/`_session.json` snapshots (LOG_DIR-lifetime semantics; `/_status` adds a `totals_since_start` delta); `_META_CWD_DONE` derived from `session_meta`. `/_end` + sweeper delete the mirror rows in step. Explicitly NOT restored (fine to lose): `_SC_FIRED`, `_PENDING_RELAY`, `_UNPRICED_WARNED`. |
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
  - Restart caveat (DEFANGED 2026-06-10 by the restart-amnesia fix): armed
    holds, replayable request bodies, totals, session identity and the warmth
    ledger ALL survive a restart now. The one thing that doesn't is
    CREDENTIALS (never persisted): restored sessions show `awaiting_auth` in
    `/_status` and pings resume only after the account's next live request
    through the proxy re-donates auth. Still avoid restarting mid-experiment
    (a few seconds of downtime + the auth gap).
  - `:7800` last restarted 2026-06-10 ~11:26 on the restart-amnesia + /_admin
    code; the `[logproxy]` ack-attribution (anti-ambush) fix went live with
    the same restart. The user hold armed until ~13:34 was carried ACROSS the
    restart by pre-seeding the new `hold_state`/`last_request` tables from the
    old process's `/_status` + newest capture (one-off migration; future
    restarts carry state automatically). Sanity-check anytime:
    `curl -s localhost:7800/_status` or eyeball `localhost:7800/_admin`.
  - (`:7799` and `8080` are the human's — leave alone.)
- **`/warm-cache` registers as a SKILL in Claude Code sessions** (the
  user-level command file `~/.claude/commands/warm-cache.md` shows up in the
  skill list). It works from ANY project whose session routes through the
  proxy; without the proxy it self-diagnoses ("proxy not active — hold NOT
  armed", one cheap real turn).
- **Git: repo initialized 2026-06-09** (first commit = post-rework code).
  `.gitignore` excludes `logs*/`, `*.out`, `warmth.sqlite*`, `_canary/`. Commit
  after meaningful changes — the lab finally has undo.
- **Key captures:** `logs_live/` (38× carriage data + subagent spawns) ·
  `logs_chatty/` (tool-trim, fat 4bcf519f / lean a0f0c609) · `logs_compact_warmth/`
  (warm fork cdcd1b7e / cold f5c27104) · split/reloc A/B pairs (`logs_split5m_on|off`,
  `logs_reloc2_on|off`) · `logs_scratch/329a6d9b-*` (hold-warm live drill
  2026-06-10: arm sentinel intercept `004-*`, auto-ping WARMED, disarm, `/_end`).

## Open / next

- (a) **DONE (2026-06-09): proxy-side replay pinger** (`WARMTH_PINGER`, on). The
  old plan ("wire `WARMTH_PING_SENTINEL` to a fork-ping") was overkill — a fork
  has to reconstruct tools/cwd/system/history just to smuggle a sentinel turn the
  proxy then recognizes. Replaced by: the proxy caches each session's exact
  post-transform last request in memory; `POST /_ping?session=<id>` replays it
  (thinking off, `max_tokens:1`) → cache read, TTL slides, ~1 output tok. The
  caller needs only the session_id. Non-warm prefixes (cold/unknown) self-skip — ping only refreshes warm (force=1 to override).
  **DONE (2026-06-10): the outer driver** — shipped IN-PROXY as the
  `/warm-cache` hold (user decision: in-band arming beats an external loop —
  the session_id rides in free, the duration is dynamic per arm). See the
  `WARMTH_HOLD` flag-table row + NOTE TO SELF. **Ping economics:** at 1h TTL
  one ping ≈ one warm read buys a full hour ≈ 19:1 → a 1h-main-agent move; the
  hold CAPS the count (24) + clamps duration (12h). At 5m it's a bad bet (~12
  pings/hour — the arming ack warns). The sentinel path (`WARMTH_PING_SENTINEL`
  + `_is_warm_ping`) is now legacy/optional — kept only because
  `WARMTH_BLOCK_COLD_PING` and `_record_warmth` still recognize it; the replay
  pinger is the recommended route.
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
- (b) **DONE (2026-06-10): statusline cache-warmth display.** Script:
  `~/tmp/proxy-sl-test/.claude/status-line.sh` — polls
  `GET /_warm?session=<sid>` (0.2s curl timeout) and renders the expiry as
  wall-clock (`🔥cache→HH:MM` / `❄️cache busted /compact` / dim `cache ∅`),
  plus dual cost (CLI stdin `.cost.total_cost_usd` vs the proxy's
  `_session.json` `est_usd`). statusline-stdin session_id == wire session_id
  (verified). Next iteration: read `/_status` for title + hold display.
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
- (g) Endpoint hardening (lab-grade today): `/_ping` `/_end` `/_warm` `/_status`
  `/_admin` are unauthenticated on localhost. Also note the proxy can now SPAWN
  a CLI turn on its own (auth self-bootstrap — bounded to 2 attempts/process,
  hold-triggered only, kill switch `WARMTH_AUTH_BOOTSTRAP=0`). `/_ping` SPENDS user credits (replays with
  cached auth headers), `force=1` can even cold-write big prefixes repeatedly;
  `/_end` lets any local proc drop state; `/_status` exposes titles/cwds/costs
  (read-only); the armed hold spends a ping (~0.10× prefix read) per TTL window
  autonomously — bounded by clamp + ping cap. Fine for a private lab box; gate
  with a token before any shared-host use. Related known gaps: cached auth
  headers can go STALE (OAuth expiry) → ping 401s, no refresh (the hold disarms
  after 2 consecutive failures); `_cache_last_request` stores at REQUEST
  time, so a failed turn leaves an unconfirmed last-request whose hash the ledger
  never stamped → pings decline (`unknown`) until the next good turn (fail-safe but
  loses pingability); ping TOCTOU (warm check vs arrival) — the hold pings inside
  the margin (default 300s), never at the TTL edge.
- (h) **DONE (2026-06-10): restart-amnesia / state reconstruction.** Shipped
  exactly per the piece-by-piece plan — see the "(persistence) restart-amnesia
  fix" row in the flag table for what persists where and the auth re-donation
  mechanism (`_ACCOUNT_AUTH`, account-level). Verified: 16 new offline checks
  in `test_warmth_store.py` (holds survive / expired reaped / secrets never on
  disk / no_auth declines / donation / stale-row reap / totals+since_start /
  cwd_done) + a live drill on `:7802` (arm → kill → restart → hold+body
  restored `awaiting_auth` → live turn from another session of the account
  donated auth → `/_ping` WARMED with cache_read=30901 on a body the new
  process never saw live). Design choices worth remembering: rows are scoped
  by `owner = LOG_DIR` so scratch ports don't double-ping the main proxy's
  sessions; restore applies the sweeper's own staleness predicate (warmth-
  ledger-based, NOT row ts) so actively-pinged sessions survive but stale rows
  are reaped instead of resurrected; `_hold_decision` gained `has_auth` so an
  auth-less hold SKIPS without burning a ping slot.

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

### fable's control-plane keys (2026-06-10, capture key-diff; user-prompted)

Fable requests carry body keys with SERVER-side semantics we can't see
structurally — "reacts to unknown keys" (user). Key diff across captures:
- `thinking: {"type": "adaptive"}` — FABLE-ONLY (haiku: enabled+31999 budget,
  opus: disabled). Unknown trigger semantics; likely implicated in SC defer
  behavior; coupled to `context_management` (clear_thinking edits 400 without
  thinking — the pinger bug). **Rule: mutate `thinking`/`context_management`
  together or not at all.**
- `output_config: {"effort":"high"}` — fable+opus (haiku none); materializes
  `CLAUDE_EFFORT` via beta `effort-2025-11-24`. If effort modulates output
  depth it scales the 5×-priced side silently. **Rule: pin CLAUDE_EFFORT in
  every A/B arm; re-derive fable output economics with effort in mind.**
- `context_management: clear_thinking_20251015, keep:"all"` — all models,
  currently a no-op that CONSTRAINS request shape (see coupling above).
- **CANARY GAP #2:** none of these keys are fingerprinted (betas/tools/system
  only) — a default flip of `effort`/`thinking.type` would never fire. Cheap
  fix alongside the heading-list idea: fingerprint control-plane keys
  (`thinking.type`, `output_config` sans schemas, `context_management` edit
  types).

### fable's server-side refusal classifier (2026-06-10, caught live by the proxy)

A user-run interactive CLI through `:7800` with a custom **workbench**
multi-agent system prompt (32k ch: `[wb:dm]/[wb:post]` intent syntax,
wrap-daemon/forge-event/transcript-path prose) sent user message "2+2" and got
hard-blocked UPSTREAM. Capture: `logs_main/396a2918-*/021`.

- **Wire shape (NEW):** `message_start` → `message_delta` with
  `stop_reason:"refusal"` and structured `stop_details: {type:"refusal",
  category:"reasoning_extraction", explanation:"…Terms of Service restrictions
  on reverse engineering or duplicating model outputs…", fallback_credit_token:
  null, fallback_has_prefill_claim:null}` → `message_stop`. ZERO content
  blocks, `thinking_tokens:0` — the model never ran; this is a classifier in
  front of it, a separate enforcement layer from text-level refusals.
  `fallback_credit_*` ties to the `fallback-credit-2026-06-01` beta — possibly
  a credit-refund path for blocked requests (null here; nothing refunded).
- **Trigger = system prompt CONTENT, not the message and not the proxy.** The
  classifier reads descriptions, not behavior: our proxy (which actually
  captures every byte) sails through because its wire shape stays canonical;
  the workbench prompt merely *describes* transcript/event logging and gets
  convicted. Keyword cluster suspected: `transcript_path` / "log every CLI
  session event" / wrap-daemon prose.
- **MODEL-GATED:** fable-only; the identical prompt runs fine on opus-4.8 and
  haiku same day ⇒ classifier sits on the new model's endpoint (asset
  protection for the newest weights, not account-level ToS enforcement).
- **Soft threshold, not a rule:** a 2-line intent-system preamble up front
  clears turn 1, but it re-fires non-deterministically on later turns as
  protocol-flavored text accumulates in context. Worst failure mode for a
  persistent agent: random mid-session kills.
- **CLI hides the truth:** the visible error is a generic "Session paused —
  Fable 5 has safety measures that flag … cybersecurity or biology topics"
  toast (+ /feedback link). Category, ToS claim, and request_id are all wire-
  only. Anyone debugging from the UI would blame their *message* forever.
- **Billing/warmth interactions:** the refused turn still bills the full
  prefix cache write (47,374 tok 1h ≈ $0.97 here), and `_record_warmth`
  correctly stamps it (`cache_creation>0` — caching DID happen). A refused
  prefix is a warm prefix; a mid-session block that forces a retry pays
  carriage twice.
- **TODO hook:** refusal counter in `_totals.json` + `[dump]` flag (see NOTE
  TO SELF). Canary can't see this — it fingerprints requests, not responses.

### fable-probe (2026-06-09, first experiment on THE consolidated proxy)

Scenario `fable-probe` in proxy-experiments (`--session <uuid>` re-verdicts free).
Capture: `logs_main/b3618fc5-*`. Procedure: seed `logs_main/_canary` from the old
`logs_compact_warmth/_canary` baselines (canary state is per-LOG_DIR and
lazy-loads on first messages request — seed BEFORE first traffic), then one
headless 4-tool fable-5 Write task through `:7800`. Verdicts:
- **Structure unchanged, TEXT IS NOT — see "Prompt families" below.** The
  canary verdict ("same 3-block structure, same 3-marker layout, 1h ttl on the
  headless main agent") is STRUCTURE-ONLY: hdr-prefix + coarse size buckets,
  and it never compares ACROSS models (namespace = model|beta). The USER then
  eyeballed the capture and found a real model-gated text difference the
  fingerprint is blind to. Beta deltas vs interactive baseline (offline fp
  diff): headless DROPS `context-1m-2025-08-07`; build stamp differs
  (`cc_version=2.1.170.3` vs `2.1.170.ba7`).
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

### Prompt families are MODEL-GATED (2026-06-09, user-spotted, verified on wire)

User noticed `# Communicating with the user` in the fable capture — absent from
opus-4.8. Verified by running opus-4.8 through `:7800` the SAME day, same CLI,
plus heading-diffs of old main-agent captures:
- **classic family** (~13–14k ch): `# System / # Doing tasks / # Executing
  actions with care / # Using your tools / # Tone and style / # Text output /
  # Session-specific guidance / # Environment / # Context management` —
  sonnet-4-6 (Jun 3) AND haiku-4-5 interactive on Jun 9 (same CLI era as fable).
- **harness family** (~2.3–3.1k ch): `# Harness / # Context management`
  (+ guidance) — opus-4.8, both Jun 6 captures and TODAY's live probe
  (`logs_main/aeea8dcd-*`). No `# Communicating with the user`.
- **harness + `# Communicating with the user`** (~6.3k ch post-strip): fable-5
  ONLY — a big new output-style/communication section (lead-with-outcome,
  readable-over-concise…). Same day, same CLI, haiku still classic ⇒ the family
  is keyed on MODEL, not CLI version. Relevant to SC prompt-strength priors
  (output-composition directives!) — re-validate SC on fable with this in mind.
- **SAMPLING TRAP (burned us in this very analysis):** the first request of a
  `claude -p` session is the 1,213-ch TITLE-GENERATOR side-call (0 tools, flat
  prompt). Naively sampling "first request per session dir" reads the title
  prompt, not the main-agent prompt — filter `tools > 0` first.
- **Canary gap exposed:** size buckets + 48-ch hdr prefix missed a multi-kchar
  section swap (and cross-model diffs are out of scope by design). CHEAP FIX
  CANDIDATE: add the `^# heading` list of each sys block to the fingerprint —
  section-level rewrites within a namespace would then fire drift.
- **Family-map nuance (sysprompt-probe arm A):** headless haiku TODAY = classic
  family too (11.9k ch, `# System/# Doing tasks/…`), so classic-vs-harness is
  model-gated in BOTH modes. The "You are an interactive agent…" OPENING LINE is
  shared by both families — it is NOT a family discriminator; use the heading
  list. (Headless classic carries no `# Environment` in system — env rides the
  msg0 bundle; interactive classic has it in system.)

### sysprompt-probe (2026-06-09): --system-prompt-file + canary positive test

Scenario `sysprompt-probe` (A=control vs B=`--system-prompt-file` sentinel,
haiku, captures `logs_main/3cdb9570*/1e8a97c5*` + rerun `ce935492*`):
- Replacement semantics: see the new bullet in "Native answers" above.
- Our transforms coexist with a custom prompt: `env_relocate` still fired on
  arm B; `system_strip` fired on the default prompt (headless classic also has
  `# Session-specific guidance`, 528 ch).
- **CANARY POSITIVE TEST PASSED, both directions:** B fired
  `structural_change` (interactive-agent block → TESTBOT, size_bucket 13→7)
  inside the namespace arm A had just touched; the RERUN's arm A then fired the
  drift BACK (TESTBOT → default). Detect-and-rebaseline works as designed. A
  deliberate prompt swap is now the canonical way to smoke-test the detector.
