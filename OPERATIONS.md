# OPERATIONS — how to run, deploy, and test proxy-lab

Pull this when you're about to touch the running proxy, cut a release, or run the
test/A-B tooling. It is **not** auto-read every turn (deliberately — port/pid/deploy
facts change often, and an auto-read doc busts msg0 on every edit). Durable
*conclusions* live in `CLAUDE.md`; the *current runtime truth* (which instance owns
:7800 right this moment, live pid/tag, in-flight experiment state) lives in
`HANDOFF.local.md`.

## Who owns :7800 (deployment model)

**:7800 is CLODEX-MANAGED-VENDORED** (the model since 2026-07-03).
clodex runs a vendored wirescope snapshot from `wb-wrap-ui/vendor/wirescope` in a
managed venv, detached, surviving GUI restarts.
`/_identity` → `"vX.Y.Z (vendored)"`; pidfile + logs under
`~/Library/Application Support/clodex/wirescope/` (captures in `.../logs/`, warmth
DB alongside).
To confirm which model is actually live, check the cwd of the :7800 LISTEN pid:
`vendor/wirescope` = managed (correct); `proxy-lab/releases/…` = a rogue hand-run
(wrong — see the 2026-07-15 incident in HANDOFF).

**Release → deploy flow under this model:**
Cutting a release here does NOT reach :7800.
clodex must re-run its own `vendor-wirescope.sh` against the new tag (pushed to
origin `avirtual/wirescope`), then a GUI restart self-applies the bump.
**That script lives in the CLODEX repo (`wb-wrap-ui`), not here — this repo cannot
run it and must not try.** Vendoring is clodex's action on clodex's timeline; a
GUI restart busts every warm prefix Bogdan has open, so it is his call to schedule,
not something a release here should imply.
So: tag + push the release, then **tell clodex** which tag to vendor, and name the
surface you touched so they can diff it rather than spot-check.
`run_release.sh` plays NO part in a prod deploy under this model — it starts a
hand-run instance, which is the thing you don't want on :7800. It still serves
dev/scratch ports.

**Legacy hand-run release model (pre-2026-07-03, still valid for dev/scratch):**
`./release.sh vX.Y.Z` cuts a release — clean tree + test suite gated, tags, builds
a `releases/<tag>` worktree, flips the `releases/current` symlink.
`./run_release.sh` starts an instance from `releases/current` (default `PORT=7800`,
`LOG_DIR`/`WARMTH_DB`/`OUT` pinned to the lab root; all three overridable, and off
:7800 an explicit `LOG_DIR` is REQUIRED so a scratch arm can't write into the
frozen `logs_main` corpus).
**Do not hand-run :7800 while clodex manages it** — you'll collide with the managed
instance and revert the model (that's exactly the Jul-2026 rogue-instance bug).

**Ownership guard (v0.6.50).** `run_release.sh` chains to `restart_proxy.sh`, whose
first act is `kill` on whatever LISTENS on `$PORT` — under the managed model that
pid is clodex's proxy, so the bare script was a foot-gun that took the live proxy
and every warm prefix with it. It now refuses unless the listener's cwd is inside
this lab: ours → proceed, no listener → proceed, anything else (including a cwd it
cannot read) → refuse, fail closed. `RUN_RELEASE_FORCE=1` overrides once you've
decided the kill is right; `RUN_RELEASE_CHECK_ONLY=1` runs only the guard.
The guard reads ownership from the LISTENER, not from config, so it stays correct
if the deployment model changes again.

## Launching proxies (dev / scratch ports)

Proxies launch via `./start_proxy.sh` / `./restart_proxy.sh` (nohup+disown → PPID 1).
A `run_in_background` Bash job dies with the CLI — never launch the proxy that way.
`start` refuses a bound port; `restart` kills + starts.
Script defaults add `STRIP_COMPACT_CACHE=1 WARMTH_BLOCK_COLD_PING=1 WARMTH_LOG_FILE=1
WS_SPAWNER_HINT=1 WS_OMIT_DEFAULT=useremail STRIP_MCP_SERVERS=claude_design`, via
`${VAR-default}` so an explicit 0/empty sticks — note this means every scratch arm
also strips the `claude_design` connector unless you pass `STRIP_MCP_SERVERS=`.
The wirescope flags are ON by canonical-script default (a fresh clone needs no
release.env) but stay OFF in CODE (library embeddings/tests unaffected).
`start_proxy.sh` also sources `release.env` (gitignored, per-machine — fills only
keys not already set, so caller env wins); `release.env.example` is the tracked
template.

**Scratch/experiment arm** = a scratch port from the dev tree:
`PORT=7802 LOG_DIR=logs_scratch <flags> ./start_proxy.sh`.
⚠️ `restart_proxy.sh` defaults `LOG_DIR=logs_main` — do NOT use it for scratch ports
(it writes into the frozen :7800 archive). Kill + `start_proxy.sh` with an explicit
LOG_DIR instead.
Sanity: `curl -s localhost:7800/_status` or `localhost:7800/_admin`.

**Teardown — HARD RULE:** to stop a proxy, target ONLY its listener PID via
`lsof -nP -tiTCP:$PORT -sTCP:LISTEN` (the `-sTCP:LISTEN` filter is essential) or a
pidfile — NEVER a bare `lsof -ti tcp:$PORT`, which also returns CLIENT sockets and
can take down clodex + the whole Electron app. Prefer the scripts (they already
filter to the listener). Signal nothing outside your own proxy process.

## Other ports / corpora

Other local ports may be in use by the operator — leave non-:7800 ports alone unless
they're your own scratch arm (e.g. a legacy logproxy writing to `logs/` — don't
touch `logs/`).
Restarts are safe-ish (state persists; only a credentials gap, which the auth
bootstrap closes) but avoid mid-experiment.
**This Claude Code session does NOT route through :7800** — drills must set
`ANTHROPIC_BASE_URL` explicitly; scratch ports have no SessionEnd hook.

Capture dirs: under the managed model, live captures go to
`~/Library/Application Support/clodex/wirescope/logs/`.
`logs_main` is the FROZEN hand-run archive (do not expect it to grow; it briefly
took live traffic Jul 12–15 during the rogue-instance window).

**DO NOT PRUNE `logs_main`** (17 GB, 796 sessions, 2026-06-09 → 07-15). Bogdan's
call, 2026-08-11, and it is a KEEP decision on the merits — not a deferral, and
not the test-fixture coupling that had been blocking it. Two independent reasons,
either sufficient:
1. **It is the BEFORE arm of every longitudinal comparison** ("how it is vs how it
   was"). It spans v0.1.0 → ~v0.6.3x, i.e. the proxy before most of the transform
   stack existed, against the same operator, box and workload as today's traffic.
   That control cannot be re-created at any price: the code that wrote it is
   tagged and re-runnable, but the 2026-06 sessions are not.
2. Three suites replay it (`test_fold.py`'s 67-session replay, `test_hints.py`,
   and `test_bust_scan.py`'s corpus invariants), and `release.sh` symlinks it into
   the release worktree — so the release gate is weaker without it.

`--older-than 30d` would take **14.49 GB of the 17 GB**, and every capture in here
is older than 30 days, so an age-based prune reads as routine hygiene and is
actually the whole archive. The prune tooling exists and works; this corpus is
simply not what it is for. Point it at live capture dirs instead.

**Provenance is by DATE, not by stamp:** captures carry model/agent/session/billing
but NO proxy version, and this dir is frozen, so a version stamp added now could
not label it retroactively. Date the sessions against `git tag --sort=creatordate`
to know which release wrote a given day. If a longitudinal claim ever needs the
proxy version inline, stamp it in the writer MID-record — `ts` is the 2nd key and
`summary` is the last, and v0.6.48's fast `/_bust` scan reads both by byte offset
(200-byte head, 4 KB tail).
`logs` = :7799.
Old experiment captures live in `logs_archive/` (logs_live, logs_chatty,
logs_compact_warmth, logs_inject, logs_codexprobe, …; retired port→corpus map in
archive-2026-06-11).
All gitignored.
Git since 2026-06-09; commit after meaningful changes.

## Client integration

Client integration ships with the proxy in `client/` (warm-cache command, statusline,
cache-expiry + cache-state hooks, settings.example.json, install.sh + README).
These are the canonical copies — edit there, cut a release to ship.

**What is actually wired on this machine** (verified 2026-08-11 — `client/` ships more
than is installed, so don't read the list above as the running config):

| Piece | State |
|---|---|
| `~/.claude/commands/warm-cache.md` | **installed** — symlink through `releases/current` (→ v0.6.49), resolves. |
| SessionEnd → `/_end` hook | **installed** user-level in `~/.claude/settings.json`, pinned to :7800. Scratch ports rely on the sweeper instead. |
| statusline | **not ours** — `~/.claude/statusline-workbench.sh` → `agent-workbench`. `client/status-line.sh` is shipped but not installed. |
| `cache-expiry-hook.sh`, `cache-state-hook.sh` | shipped, **not installed** (no hook entry references them). |
| repo `.claude/` | **empty.** Nothing here references `releases/current/client/...`, so the "wiring upgrades with releases" property holds only for the warm-cache symlink. |

Installing the rest is `client/install.sh` + `client/settings.example.json`; nothing
depends on it (everything fails soft — proxy down ⇒ statusline renders `cache ∅`,
hooks exit 0).

## Test suites (gate every release)

`release.sh` runs **every `test_*.py` in the repo root** (glob, since 2026-07-19 — a new suite is gated the day it lands; before that an explicit six-suite list had silently omitted test_bake/test_pot).
All are offline script-style suites (`python3 test_X.py`, exit 0 + `ALL PASS`; not pytest).

**There is deliberately no list of them here.** One lived here and drifted twice — it omitted `test_bake`/`test_pot` (which is why the gate became a glob in the first place), and by 2026-08-11 it was 5 suites short again. A hand-maintained enumeration of a globbed set is a second source of truth that only ever decays, and the version that decays silently is the one in the doc nobody runs. So:

```sh
ls test_*.py                                   # what the gate runs, always current
head -8 test_<name>.py                         # what that suite is for
grep -l "<thing you touched>" test_*.py        # which suite covers your edit
```

Every suite opens with a docstring stating **what defect it guards** (several also say what the *previous* version of the check failed to catch — worth reading before you add a case). That is the authoritative description; run the one whose area you edited, and `./release.sh` runs all of them regardless.

## Offline analysis + A/B proof tooling

These read a capture dir offline and never touch a running proxy, so they are safe against the live `LOG_DIR`.

`analyze_tools.py` — offline tool-utilization ledger: `python3 analyze_tools.py <dir> --by role|session`. Prices deadweight (the live per-session twin is `/_context?utilization=1`).

`analyze_churn.py` — read/edit churn ledger: `python3 analyze_churn.py <dir> [--by tool|session] [--top N] [--bash]`. Ranks tool_results by re-carriage cost — how much a result costs *per turn* to keep re-shipping in the cached prefix, which is the number that decides whether a transform is worth writing. Detects the `STRIP_PRIOR_READS` marker so a pre-stripped corpus is reported rather than silently miscounted.

`analyze_truncations.py` — mid-stream truncation rate, and optional attribution of cuts to an externally-recorded event log: `python3 analyze_truncations.py <dir> [--events FILE] [--eligible-from ISO]`. Read the docstring before quoting any number out of it: it records the four predicate passes (every wrong one *inflated* the rate) and the three denominator traps, because the analysis is almost entirely in choosing what counts, not in the counting. Runs a randomization negative control by default and warns when the observed count sits inside the chance band. Baseline 2026-08-11: **173/76,768 = 0.226%** of started streams never finish.

**Reviewer-corpus tooling** (four scripts behind `REVIEWER_OPTIMIZATION.md`, which answers "can a richer bootstrap buy the reviewer fewer requests?" — verdict: no, and the reason is relevance, not cost). All four take `--logs DIR` and default to the live clodex capture dir, so they run against any corpus, not just the one they were written for:
- `analyze_reviewer.py` — the corpus walk + lever pricing (~10 min); `--json OUT` emits one row per session, which is what the other scripts consume.
- `reviewer_stats.py rows.json` — distributions over those rows (percentiles by nearest rank, not interpolated — at n=64 an interpolated p90 invents a session that does not exist).
- `reviewer_output.py` — splits the output bill into the final deliverable vs per-round loop output, using `output_tokens_details.thinking_tokens` off the wire.
- `reviewer_batchable.py` — classifies adjacent round pairs as batchable or causally DEPENDENT (next call's target appears in the prior result), so "just batch them" is priced against what was actually independent.

**A/B proof harness (transforms vs verbatim passthrough):**
- `ab_run.py "PROMPT" --a-url … --a-dir … --b-url … --b-dir … -n N -o run.json` —
  drives the SAME `claude -p` task through two arms N times (A then B per rep),
  records each `claude --output-format json` session_id + CLI cost cross-check into a
  manifest. Probes `/_identity` to warn if the control arm isn't actually passthrough.
- `ab_analyze.py DIR_A DIR_B [--last N|--since 30m|--manifest run.json]` — prices both
  corpora from the captured `billing` blocks (real wire tokens + TTL-correct est_$,
  NOT the CLI's under-report), side-by-side with deltas, split main vs subagent, plus
  the treatment's fired-transform tally. Headline = carriage % and $ % vs the control.
- Control arm = a second port from THIS binary started with `WIRESCOPE_PASSTHROUGH=1`
  + its own `LOG_DIR` (no clone — zero code drift).
