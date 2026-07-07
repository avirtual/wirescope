# Changelog

All notable changes to wirescope, newest first.
Convention: add the new version's entry at the top of the release-history section as part of cutting the release (release.sh warns if the tag being cut has no entry here).
One entry per tag; a line per meaningful change; measurements inline where they justify the change.
Deep rationale lives in the module docstrings and INTEGRATION.md / SUBSCRIBERS.md / WIRESCOPE.md — this file is the "what changed when" index.

## v0.6.25 — 2026-07-07

- **PIN_SETTLED_BREAKPOINT** (default ON, `=0` to disable): pin a `cache_control` breakpoint on the *penultimate* real-user boundary — the last user turn of the settled region. At every turn transition (and on resume) the request's only message breakpoints are msg0 + the fresh tail; the still-valid cache entry sits at the previous turn's boundary (cached by that turn's first-request tail marker), beyond the API's hit-check window when the tool loop was long → read collapses to msg0/system. The pin makes it an exact entry match at any loop length. A/B wire-proof (pin vs passthrough-pin control, same binary, identical 59-msg transition): cache_read 13,349 / write 6,288 vs read 5,987 / write 13,665 — the control re-wrote the entire message region. Anchor derives from the same `_settled_boundary` detector as the prior-turn strips (structurally impossible to disagree; suite-enforced). Budget ≤4 with msg0/bundle-marker migration; never steals a system or tail marker; decline paths never mutate; skipped when the cold-compact strip just reclaimed message markers. Per-request record: `pin_settled_breakpoint`.
- Bare-string anchors (the CLI rewrites its block-form tail prompt to a bare string once it settles into history — wire-proven) are converted back to a text block when pinning. Safe: **string ≡ `[{type:text}]` for cache identity**, wire-proven on the control arm (bridged to a block-form-written entry through the string rewrite with no proxy help).
- Measured the API hit-check window behind a breakpoint: **bridges at 11 blocks, collapses at 26** (docs say ~20; previously inferred from a single incident).
- Wire fact for the record: **CLI `--resume` replays prior thinking verbatim** — strip-OFF sessions resume byte-identical; the "resume sheds thinking" divergence seen in consumer captures was the live thinking-strip acting on the resumed request.
- `bake_session` moved into the `proxylab/` package (root file is now a compat shim): vendor drops that copy only `proxylab/` were missing it, so `POST /_compact` 500'd with ModuleNotFoundError and consumer compact-on-resume silently no-op'd.

## v0.6.24 — 2026-07-07

- **SIDECALL_MODEL** (default ON → `claude-sonnet-5`, `=off` to disable): downshift the CLI's two one-shot utility side-calls — WebFetch page-summarize and WebSearch — which the CLI ships on the *session's main model*, ignoring the invoking subagent's model and WebFetch's own "small fast model" contract. Wire-proven: sonnet AND haiku subagents' fetch summaries both arrived as fable-5; one research run's 75 side-calls were $13.92 of $19.93 total. Cache-safe (side-calls carry zero cache_control), never upshifts, strict two-family shape match with decline-on-doubt. Drops `output_config`, strips the `context-1m` beta header on fire.
- `/_status` `sub_agents[].display_name` now populates for named spawns from the agent-id header's pre-`@` name part (directive name still wins; anonymous UUID blobs skipped) — consumers' label chain is simply `display_name → role`.

## v0.6.23 — 2026-07-07

- **Capture-dir retention: `GET/POST /_prune`** + offline CLI twin (`python3 -m proxylab.prune`). Two-tier: `tier=receipts` drops request bodies + SSE (~95% of bytes) while keeping billing receipts so `/_report` still prices cold sessions forever; `tier=full` removes session dirs. Age = the dir's newest-file mtime; warm/held/recent sessions never touched; on-demand only (no background sweep); explicit `older_than` with dry-run default.

## v0.6.22 — 2026-07-07

- **Per-agent-line cost decomposition (`by_line`)**: where did a session's money go. `cost.est_usd` stays the whole session tree (subagents share the parent session_id — verified cent-exact vs disk); new `cost.main_est_usd` + `sub_agents[].est_usd` split it per line, `/_report scope.agents[].est_usd` is the disk twin for cold/pre-feature sessions, `/_admin` renders both. Motivating measurement: a headless research run was 98% subagent cost.

## v0.6.21 — 2026-07-06

- Bust summary: attribute deploy-tax busts via `restart_between` so consumer chips can exclude them (calibration follow-up to the classifier).

## v0.6.20 — 2026-07-06

- `/_session`: bust-jump navigation + top-of-page navigator entry.
- `/_bust`: thread class/fault/fix_hint + `restart_between` per transition.

## v0.6.19 — 2026-07-06

- Bust classifier: split `compact` (expected one-time contraction) from `conversation` (a mid-history flap) — cry-wolf fix from clodex review.

## v0.6.18 — 2026-07-06

- Bust classifier: live per-request **real-bust** counter in `/_status` (`busts` rollup: six structural classes, each with fault ∈ self/content/environment + fix_hint); compares the FULL system[] so post-marker changes never misfile as `conversation`.

## v0.6.17 — 2026-07-06

- `STRIP_CURRENT_DATE`: remove the volatile date line from the cached prefix — zero midnight-rollover busts (previously: read 26.6k / write 163.2k on the first turn past midnight).
- Bust locator: disk-based cache-divergence forensics + `/_session` turn navigator.

## v0.6.16 — 2026-07-05

- `STRIP_TASK_REMINDERS` rides the per-session L2 strip level instead of a global flag (an agent you want nudged toward tasks just stays below L2).

## v0.6.15 — 2026-07-05

- `STRIP_TASK_REMINDERS`: skip the CLI's accreting "task tools haven't been used" nag blocks (~105 tok each, 4+/session observed, 0% Task-uptake after).
- release.sh pushes main + tag to origin at cut; `/_session` falls back to disk captures for cold sessions.

## v0.6.14 — 2026-07-05

- `RELOCATE_SCRATCHPAD_TO_TAIL`: peel the per-session scratchpad UUID path out of the static prefix (CLI ~2.1.19x embeds it mid-block → busted every fresh same-project instance).
- Spawner hint: migrate the last system cache marker onto the hint block.
- Price `claude-sonnet-5` (intro rate through 2026-08-31).

## v0.6.13 — 2026-06-22

- `STRIP_MCP_SERVERS`: surgically drop a named MCP server's whole tool family from tools[] (the targeted alternative to `--strict-mcp-config`); wire-proven 49→29 tools. Per-agent re-admit via `[wirescope:keep-mcp]`; advertised on `/_identity`.

## v0.6.10 – v0.6.12 — 2026-06-22

- `POST /_compact`: offline transcript bake over HTTP (thinking-only v1; integrity-gated, atomic, idempotent; `wire_delta` classifies the reclaim as live vs wire-carried). `pure_thinking_turns` divergence predictor.
- `/_status`: per-session strip state for consumer reconciliation.

## v0.6.8 – v0.6.9 — 2026-06-21

- Read+Edit fold moved into strip level L2; `bake_session.py` offline JSONL optimizer (proxy-free thinking strip).
- `GET /_subagents`: per-subagent popover detail; `sub_agents[].last_active_s`.

## v0.6.5 – v0.6.7 — 2026-06-20

- Read+Edit **fold transform**: apply the edit onto the Read body so downstream turns see the file's final shape; multi-chunk per-file folding; directive-before-strip bug fixed.

## v0.6.0 – v0.6.4 — 2026-06-19/20

- Codex/openai wire: proxy WebSocket responses, capture every turn on a persistent WS, reconstruct full transcripts for `/_session`.
- Strip fixes: deliberate level changes take effect; override establishes the guard latch on sight.

## v0.5.0 — 2026-06-19

- `/_identity` advertises the L2 strip gate (`strip_thinking.max_level`).

## v0.4.33 – v0.4.39 — 2026-06-19 (the L2 strip arc)

- Edit-ack collapse (`STRIP_PRIOR_EDIT_ACKS`), gated to free-ride a thinking-strip bust (originating its own bust is a ~1400-turn loss).
- `STRIP_PRIOR_TOOL_ERRORS` + the L2 strip level (L2 = L1 + bust-riders).
- Sticky cold-gated **guard latch** (anti-flap): strip/no-strip decided once per session, latched, established only on a cold prefix — fixed live-confirmed mid-session 42k/49k-tok busts from per-turn recompute wobble.
- Prior-Read clear tried and removed (dead-end experiment).

## v0.4.29 – v0.4.32 — 2026-06-18

- **`STRIP_PRIOR_THINKING`**: drop thinking blocks from completed prior turns (signed current turn untouched). Measured live: ~223k→130k window, ~40% of the wire/turn; ~16–22% of bill on the worst-case corpus. Ships OFF; per-session consumer opt-in via directive or `/_strip`; override persists in warmth.sqlite.

## v0.4.24 – v0.4.28 — 2026-06-17/18

- `/_timeline`: cost-evolution dashboard (read/write/generation spine + carriage drill-down); `/_report?detail=1` per-request series.
- Starlette 1.x lifespan migration + dep floors; quota/health probes no longer clobber the durable session view; timeline float-loop hang fixed.

## v0.4.18 – v0.4.23 — 2026-06-17 (the /_report arc)

- `GET /_report`: disk-based per-session cost/efficiency report, iterated to v4: waste section + marginal cache-miss pricing, carriage multiplier = requests (not turns), claudemd/useremail carriage scoped to subagents, per-line write rates (1h main / 5m sub).

## v0.4.9 – v0.4.17 — 2026-06-16 (the /_context arc)

- `GET /_context`: per-session tool rosters → per-category composition breakdown → `&utilization=1` deadweight pricing → agents+skills split out of system → per-skill roster/utilization parity. Both wire shapes detected (wrapped reminder + opus-4-8 mid-conversation-system).
- `skillOverrides:{name:off}` wire-confirmed to reclaim tokens (fresh-conversation caveat documented).
- start_proxy.sh sources release.env; spawner-hint + omit-default become canonical script defaults.

## v0.4.8 — 2026-06-15

- **`WIRESCOPE_PASSTHROUGH`** master control-arm flag + the A/B proof harness (`ab_run.py`/`ab_analyze.py`, experiments/). Durable conclusions: subagent shaping is the robust win (−45% carried / −21% cost on realistic fan-out); main-line transforms' value is cross-instance prefix sharing (5/5 deterministic share), so they stay default-on and un-gated.

## v0.4.0 – v0.4.7 — 2026-06-14 (wirescope v1 directive protocol)

- `[ws:...]` → `[wirescope:...]`: agent-name/omit/keep/replace + spawn directives + tool-roster trim verbs (tools/strip-tools/keep-tools); sticky per-instance spawn memory bound to a lineage fingerprint; operator default omit policy (`WS_OMIT_DEFAULT`); opt-in spawner discovery hint (`WS_SPAWNER_HINT`); INTEGRATION.md ships as the front-door contract.
- Subagent attribution hardened: leaked-parent turns rejected via content fingerprint.

## v0.3.6 – v0.3.11 — 2026-06-14

- Subagent classification (cc_is_subagent header), keyed by `x-claude-code-agent-id` so concurrent spawns stay distinct; opt-in display names; the first `[ws:...]` directives (agent-name + omit, WIRESCOPE.md).

## v0.3.0 – v0.3.5 — 2026-06-13/14

- **Initial public release as wirescope.** requirements.txt + one-line install; `context.input_tokens` on `/_status`; cold-resume tracking; /_admin layout iterations.

## v0.2.x — 2026-06-12/13

- **Subscriber push feed** (`/_subscribe`, SUBSCRIBERS.md): text.delta / turn.completed / session.ended, loopback-gated, persisted registry.
- App-specific intent parsing retired (wb.py deleted) — the proxy is app-agnostic.
- Shared SQLite store split out (`store.py`, per-module schema registry); `receipts.py` = the one turn-finalize convergence; lazy package `__init__` (proxylab importable as a library).
- Codex/openai pricing into the shared ledger; proxy self-reports its version; `/_identity` handshake; programmatic `/_hold`; refusal surfacing; leading cache-breakpoint tracking.

## v0.1.0 – v0.1.1 — 2026-06-11

- **First tagged release** (repo since 2026-06-09): the transparent analytical forward-proxy — capture, billing, two-state SQLite warmth ledger, keep-warm pinger + hold driver with auth bootstrap, restart-amnesia persistence, `/_status`/`/_admin`/`/_session`, codex routing, transforms (relocate/strip/sort/inject/shortcircuit), release machinery (releases/current worktree model), client integration (`client/`: /warm-cache command, statusline, hooks).

---

Pre-tag history (2026-06-09/10: fable probes, refusal classifier discovery, control-plane coupling, prompt-family gating) is journaled in the local archives, not in git.
