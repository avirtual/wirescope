# experiments — proving wirescope saves what it claims

This folder holds reproducible experiments that measure what wirescope's wire-payload transforms actually buy, against an honest baseline.
The scripts and fixtures here are the receipts behind the numbers in the README/CLAUDE.md.
They are written to be re-run, and — more importantly — to explain the *why*, because the reasoning is subtle and is not obvious from the code alone.

If you read nothing else, read **"The three findings"** below.
They are the non-obvious conclusions; everything else is scaffolding to support them.

## The thesis under test

The project thesis is that **cost is context *carriage*, not the model thinking**.
A turn pays to ship (and re-ship) its prefix — tools, system prompt, history — every single turn, whether the model uses it or not.
So the win condition is "ship fewer bytes / ship cheaper bytes," and the way to prove it is to run the *same workload* two ways and compare the bytes on the wire.

## The harness: a passthrough control on the same binary

The hard part of an honest A/B is the control.
We compare two arms:

- **TREATMENT** — a normal wirescope port: transforms on (omit / tool-trim / relocate-env / strip-sections / sort).
- **CONTROL** — *the same binary* started with `WIRESCOPE_PASSTHROUGH=1`, which skips the entire request-mutation chain so the forwarded bytes equal the received bytes. Capture, billing, warmth and the subscriber feed still run (they only read the payload), so we get the wire numbers for a byte-verbatim forward.

**Why a flag on the same binary, and not a clone with features turned off?**
Because a clone drifts.
The moment the control is a separate checkout (or :7800's older release), any difference you measure is confounded by code differences you didn't intend.
`WIRESCOPE_PASSTHROUGH` is one guard wrapping the one contiguous mutation block in `server.py`, exposed as `capabilities.passthrough` on `/_identity` — so the analyzer and `ab_run.py` can *verify* the control arm is actually a verbatim forwarder before trusting a single number.
Zero code drift is the entire point: the only difference between the arms is whether the transforms ran.

Two scripts at the repo root drive it:

- **`ab_run.py`** — runs `ANTHROPIC_BASE_URL=<arm> claude -p "<prompt>" --output-format json` for arm A then arm B, N times, and records each session id into a manifest. It probes `/_identity` and warns if the control arm isn't actually passthrough. It has `--b-prompt` so the two arms can solve the *same problem with different prompts* (see methodology below).
- **`ab_analyze.py`** — prices two capture corpora from each response's `billing` block (real wire tokens + TTL-correct est_$, **not** the CLI's `total_cost_usd`, which under-reports 1h writes). Side-by-side totals, per-session means, a main-line-vs-subagent split, the treatment's fired-transform tally, and a carriage-% / $-% headline. Scope it with `DIR_A DIR_B`, `--last N`, `--since`, or `--manifest run.json`.

## Methodology rules (these are load-bearing — don't skip)

1. **Don't run the identical prompt through both arms.**
   That measures nothing interesting, because wirescope's value is letting you solve a problem in a way a vanilla run *can't*.
   Instead, both arms solve the *same problem*; the treatment prompt instructs the spawner to add per-subagent wirescope directives (`[wirescope:tools …]`, `[wirescope:omit …]`) that a vanilla run has no way to express.
   `ab_run.py --b-prompt` exists for exactly this.

2. **Don't cook the baseline.**
   The control is *not* a naive run with all ~33 default tools loaded.
   It's a developer who already trimmed sensibly (`--tools "Read Edit Write Bash Glob Grep Task"`, Workflow and friends gone).
   We measure only the *incremental* gain wirescope adds on top of a reasonable setup, never the gap against a strawman nobody would actually use.

3. **Run N≥5 and report warm steady-state, not a single cold run.**
   A custom `ANTHROPIC_BASE_URL` puts you on the org cache scope, where the *vanilla* prefix is typically already warm from other traffic.
   The treatment's transformed prefix starts cold, so rep 1 pays a full cold write while the control reads a pre-warmed prefix at 0.1× — a single cold run can show the treatment *losing by 130%+*, the exact opposite of the truth.
   The win only appears once the transformed prefix is itself warm (the normal operating condition).
   So: run several reps and drop rep 1 (`--last N-1`).
   **Carriage (tokens shipped) is the warmth-independent honest metric; $ is confounded by cache history** — lead with carriage, report $ as the steady-state secondary.

## The experiment: `subagent-ab/`

A realistic fan-out.
The main agent is told: for each Python file under `./src`, spawn one general-purpose subagent to read that file and list its defs, then combine the results.
The fixture (`fixtures/orderflow/`) is a small but *realistic* project: four source files plus a project `CLAUDE.md` that is genuinely useful to a developer (build/test/conventions/architecture) but **useless to a subagent whose only job is to read one file and list its functions**.

The treatment prompt makes the spawner prefix each subagent with:

```
[wirescope:tools Read,Glob]
[wirescope:omit claudemd,useremail]
```

— a per-subagent tool roster trim plus dropping the project `CLAUDE.md` and the user-email block from that subagent's context.
A vanilla run cannot shape a subagent's context this way; `--tools` only reaches the main agent, and there is no native per-subagent CLAUDE.md suppression.

**Run it:**

```bash
# 1) bring up the two scratch arms (NEVER :7800 — that's the live proxy):
PORT=7802 LOG_DIR=logs_ab_treat WS_OMIT_DEFAULT=claudemd,useremail ./start_proxy.sh
PORT=7803 LOG_DIR=logs_ab_ctrl  WIRESCOPE_PASSTHROUGH=1            ./start_proxy.sh

# 2) run the experiment (defaults: sonnet, 6 reps):
experiments/subagent-ab/run.sh
#   MODEL=claude-opus-4-8 REPS=8 experiments/subagent-ab/run.sh   # to vary
```

`run.sh` refuses to run unless 7802 reports `passthrough=false` and 7803 reports `passthrough=true`, prints the full N-rep table, then re-prints the warm steady-state (rep 1 dropped).
The `logs_ab_*` capture dirs are gitignored; the fixtures, prompts, and scripts are tracked.

## Headline result

Sonnet, the realistic baseline above, warm steady-state, N=6:

| arm split | carriage | cost |
|---|---|---|
| **overall** | **−45%** | **−21%** |
| subagent line | −69% | −43% |
| main line | ~flat | **+10.5%** |

The win is **entirely the subagent line**: trimming ~200 tool schemas and omitting the ~1.4k-char project `CLAUDE.md` (plus the user-email block) from every file-reading subagent.
The main line goes the *wrong* way even in steady state (+10.5% $; +24% if you include the cold rep 1) — that is the one-time cost of reshaping the prefix, and it is the subject of finding 3.

## The three findings (the non-obvious WHYs)

### 1. Passthrough is the only honest control, and both arms must be the same binary.
Covered above, but it bears repeating because it's the methodological foundation: a clone or an older release as "control" measures code drift, not transforms.
The flag-on-same-binary design is what lets us claim the *only* difference between the arms is the mutation chain.

### 2. The dollar sign is governed by a model-specific cache threshold; carriage is not.
The minimum-cacheable prefix is **model-specific: 1024 tokens on Sonnet/Opus, 2048 tokens on Haiku.**
It applies to the *cumulative* prefix (tools → system → messages up to a breakpoint), not the increment between two markers.

Main agents carry a large tools+system prefix (~8–9k tok), so they never fall below the threshold; the only thing that *can* is a hard-trimmed subagent.
Our trimmed subagent prefix was ~1,559 tok:

- On **Sonnet/Opus** that's comfortably over 1024 → the trimmed prefix is still **cached** → wirescope reads a *smaller* cached prefix → it wins on carriage **and** $.
- On **Haiku** that's under 2048 → the trimmed prefix ships **uncached** at full 1.0× rate → the 0.1× cache-read discount on the (larger, un-trimmed) control prefix can *beat* the smaller-but-uncached treatment prefix.

That is the whole story behind why the same experiment showed **−21% on Sonnet** but **+16% (a backfire) on Haiku**.
Note what it is *not*: it is not "Haiku has a small system prompt" (an earlier wrong guess of mine).
The subagent system prompt was byte-identical (2613 chars) on both models — the difference is purely the 2048-vs-1024 threshold.

**Takeaway:** carriage (−45%) is the robust, model-independent claim.
The $ figure depends on whether your trim keeps the prefix above that model's threshold.
For real projects on Sonnet/Opus it does; this is also why "nobody uses Haiku for real projects" makes the −21% the representative number.

### 3. The biggest payoff is cross-worktree cache *sharing* — which this single-agent A/B structurally cannot measure.
This is the one to internalize.
The stock CLI bakes *machine-local* information **into the cache-marked prefix**:

- `# Environment` (absolute cwd + git branch/status) sits inside the marked `system[]` system-prose block.
- The absolute path stamp `Contents of /Users/.../CLAUDE.md` sits inside the marked `messages[0]` bundle.

Because caching is cumulative-prefix, that local difference busts the system-prose breakpoint **and everything after it**.
So two instances of the *same agent* on the *same project* but in *different git worktrees/branches* share only the first breakpoint (tools, ~5.8k tok); each one **cold-writes** the ~3.5k tok of *byte-identical* system prose + `CLAUDE.md`, purely because a cwd string and a git flag differ.

Wirescope's `RELOCATE_ENV_TO_TAIL` + `RELOCATE_CLAUDEMD_PATHSTAMP` peel `# Environment` and `# currentDate` out of those marked segments and normalize the path stamp, so the system prose and `CLAUDE.md` become **byte-identical across worktrees** → shareable as cheap 0.1× reads instead of per-instance cold writes; the volatile bits go to an uncached tail.

The single-agent / single-cwd A/B only ever sees the *one-time reshape cost* — that is the positive main line in the headline (+10.5% $ in steady state, +24% if you count the cold rep).
It **cannot** see the payoff, which is instance 2..N reading the shared segment instead of cold-writing their own copy.
This is the clodex pattern exactly: many agents, one project, different branches.
The benefit compounds with the number of concurrent instances, which is why it's the headline benefit in practice even though our A/B can't price it.

**Corollary (open question):** on a *single short* session the main-line transforms (relocate/strip/sort) are net-negative — they bust the globally-warm vanilla prefix without enough reuse to amortize.
Their value is precisely the cross-instance sharing above plus long-session amortization.
Whether they should be warmth-gated or left default-on is a real decision, pending the worktree probe (next) and a long multi-turn measurement.

## `worktree-sharing/` — the payoff finding 2's A/B can't price

The probe for finding 3 (built; see `worktree-sharing/README.md`).
Two git worktrees of one project, the same agent in each, comparing the *second* instance's first-turn cache behaviour.
Result (sonnet, real `claude -p`, 5 reps, deterministic — zero variance): under the stock control the two worktrees' system segments have **identical length but different hashes** in every rep — the cwd string alone busts the whole ~8k-token system write, so instance B cold-writes ~7,998 tokens (segment shared 0/5).
Under wirescope the cwd is relocated to an uncached tail, the segments are byte-identical (same hash, and the *same* hash across all reps), and instance B *reads* 5,536 tokens instead of cold-writing them (writes just ~942; shared 5/5).
That is the compounding cross-instance benefit a one-cwd A/B structurally cannot see.

## What's not here yet

- A long multi-turn main-session run to measure relocate amortization on one long-lived agent. *(optional)*

## File map

```
experiments/
  README.md                         <- you are here (methodology + the 3 findings)
  subagent-ab/
    run.sh                          <- safe runner (scratch ports only, never :7800)
    prompts/treatment.txt           <- spawner adds per-subagent wirescope directives
    prompts/control.txt             <- same problem, vanilla spawn
    fixtures/orderflow/
      CLAUDE.md                     <- realistic project doc (useless to a file-reading sub)
      src/{validator,pricing,promotions,emitter}.py
  worktree-sharing/
    README.md                       <- finding 3 in detail + the result
    run.sh                          <- builds two worktrees, runs claude -p in each, prices it
    probe.py                        <- compares the system-segment hash + cache_read across worktrees
../ab_run.py                        <- the A-then-B rep driver (kept at repo root; general tool)
../ab_analyze.py                    <- the offline pricer over two corpora
```
