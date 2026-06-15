# worktree-sharing — pricing the payoff the single-agent A/B can't

This is the experiment behind **finding 3** in `../README.md`: the biggest practical benefit of the main-line transforms is cross-worktree cache *sharing*, and a single-agent A/B structurally cannot see it.

## The claim

The stock CLI bakes machine-local information **into the cache-marked prefix**:

- `# Environment` (absolute cwd + git state) rides inside the cache-marked `system[]` system-prose block.
- the absolute `Contents of /Users/.../CLAUDE.md` path stamp rides inside the cache-marked `messages[0]` bundle.

Caching is cumulative-prefix, so that local difference busts the system breakpoint **and everything after it**.
Two instances of the *same agent* on the *same project* but in *different git worktrees* therefore can't share the system-prose / CLAUDE.md segments — each cold-writes a byte-identical copy, purely because a cwd string and a git flag differ.

wirescope's `RELOCATE_ENV_TO_TAIL` + `RELOCATE_CLAUDEMD_PATHSTAMP` peel those volatile bits out to an uncached tail, so the segments become byte-identical across worktrees and share as cheap 0.1× reads.

## How the probe works

`run.sh` builds two git worktrees of one project (the `subagent-ab` orderflow fixture — same files, different cwd + branch), runs a trivial first-turn `claude -p` in each through both arms (treatment 7802 / control 7803), and feeds the four sessions to `probe.py`.
A is run before B in each arm, so A warms the prefix and B reads it *iff* the segment is shareable.
`probe.py` reports, per arm: whether the cache-marked system segment hash is identical across the two worktrees (the structural claim), and instance-B's first-turn `cache_read` vs `cache_write` (the economic claim).

```bash
# arms must be up (same as subagent-ab; NEVER :7800):
PORT=7802 LOG_DIR=logs_ab_treat WS_OMIT_DEFAULT=claudemd,useremail ./start_proxy.sh
PORT=7803 LOG_DIR=logs_ab_ctrl  WIRESCOPE_PASSTHROUGH=1            ./start_proxy.sh

experiments/worktree-sharing/run.sh          # MODEL=… to vary; sonnet default
```

## Result (sonnet, real `claude -p` first turns)

| arm | system segment (hash · chars) | instance-B cache_read | instance-B cold write |
|---|---|---|---|
| **CONTROL** (stock bytes) | A `fccd7d16` · 13474 / B `893e2e87` · 13474 | 2,182 (tools only) | **8,004** |
| **TREATMENT** (relocate) | A `aab31357` · 11812 / B `aab31357` · 11812 | **5,536** | 946 |

The cleanest line in the whole experiment is the **control's two hashes: identical length (13474 chars), different value.**
The two worktrees' system blocks differ by *nothing but the embedded cwd string* — and that alone busts the entire ~8k-token system write on the second instance.
Under treatment the cwd is relocated to an uncached tail, so both worktrees produce the *same* system segment (`aab31357`) → instance B reads it (5,536) instead of cold-writing it (946 vs 8,004).

`env=relocated→tail` and `claudemd-stamp=normalized` in the treatment rows (vs `in system[]` / `abs-path` for control) confirm *why*: the volatile bits left the cached prefix.

## Why this matters more than the A/B headline

The single-agent A/B (`../subagent-ab/`) only ever ran one cwd, so it saw the main-line transforms as a *cost* (+10.5% $ — the one-time reshape).
This probe shows the other side of that trade: every *additional* instance on a different branch/worktree reads the shared segment instead of cold-writing its own.
The benefit compounds with the number of concurrent instances — which is exactly the clodex pattern (many agents, one project, different branches).
So the main-line transforms aren't net-negative; their payoff just lives in a dimension a one-cwd experiment can't measure, and this is that measurement.

## Caveat

`cache_read` on instance B depends on instance A's write still being warm (same 5-minute TTL window — `run.sh` runs A then B back-to-back, so it is).
The *structural* claim (segment hash A==B under treatment, A!=B under control) is warmth-independent and is the load-bearing proof; the `cache_read`/`cache_write` numbers are the economic illustration of it.
