# long-session — do the MAIN-LINE transforms pay off within one long session?

This experiment isolates the always-on **main-line** transforms (relocate-env-to-tail, strip-system-sections, sort-tools, claudemd-pathstamp) over a single long-lived session — *one* stable cwd, *no* subagents, *no* worktrees.
It's the other half of the main-line story: `../worktree-sharing/` proved the *cross-instance* payoff; this asks whether there's *also* a *within-session* payoff (the one-time prefix reshape amortized over many turns), or whether the main-line transforms are single-session-neutral and their entire value is cross-instance.

## How it works

`run.sh` copies the orderflow fixture into one fresh git repo, then drives a fixed 14-turn prompt sequence (`prompts.txt` — read four files, then reason over them) as **one resumed `claude -p` session** through each arm (treatment 7802 / control 7803).
`trajectory.py` pairs each main-line request with its response, orders by seq, and prints the per-step and **cumulative** est_$ + carried tokens for both arms side by side, with the running treatment−control delta and the crossover step.

```bash
# arms up (same as the other experiments; NEVER :7800):
PORT=7802 LOG_DIR=logs_ab_treat WS_OMIT_DEFAULT=claudemd,useremail ./start_proxy.sh
PORT=7803 LOG_DIR=logs_ab_ctrl  WIRESCOPE_PASSTHROUGH=1            ./start_proxy.sh

experiments/long-session/run.sh        # MODEL=… to vary; sonnet default
```

## Result (sonnet, 14 turns → 18 main-line steps each)

| metric | treatment | control | Δ |
|---|---|---|---|
| cumulative est_$ | $0.071 | $0.092 | **−22%** |
| cumulative carried | 136,344 | 142,731 | **−4.5%** |

But the trajectory is the real story, and it is **not** amortization:

```
 step    TREAT $    cum       CTRL $    cum       cumΔ$      T.read T.write  C.read C.write
   1     0.00642  0.00642    0.02656  0.02656   -0.02014     5536    932    2182    7982
   2     0.00504  0.01145    0.00506  0.03162   -0.02017     6002    668    6173     668
  ...
  18     0.00407  0.07146    0.00422  0.09188   -0.02042     8044    100    8192      76
```

The cumulative dollar gap is **−$0.020 at step 1 and still −$0.020 at step 18** — it barely moves.
**Almost the entire cost win is the turn-1 step, and that turn-1 step is the cross-instance effect, not amortization:**

- Treatment turn 1: `read=5536, write=932` — it *read* its prefix warm. That 5,536 is exactly the shared-prefix read from `../worktree-sharing/`: treatment's env-relocated system prefix is cwd-independent, so it was already globally warm from earlier runs.
- Control turn 1: `read=2182, write=7982` — it *cold-wrote* the system prose, because the stock prefix embeds this fresh cwd and so is novel.

After turn 1, the per-step costs are nearly identical (e.g. step 10: $0.00292 vs $0.00297).
The only steady-state difference is that treatment ships **~156 fewer tokens per turn** (`T.read` consistently ~156 below `C.read`) — that's the stripped `# Session-specific guidance` section, worth ~$0.00005/turn.
Relocating `# Environment` is **per-turn cost-neutral within a session**: it moves the bytes from the system block to a tail message that is still carried every turn; it doesn't remove them.

## What this means (honest read)

- **There is no meaningful single-session amortization.** Within one stable-cwd session, the main-line transforms are roughly cost-neutral per turn after the first; the only genuine per-turn saving is the small `strip-system-sections` trim (~2% of carriage).
- **The main-line transforms' value is cross-instance, full stop.** The −22% here is the *same* cross-worktree/cross-instance sharing from `../worktree-sharing/`, surfacing on turn 1 because treatment's system prefix is globally shared while every fresh stock cwd cold-writes its own. It is not a within-session effect.
- **Implication for the warmth-gating question:** do **not** warmth-gate the main-line transforms per session. Their payoff *requires* the reshaped prefix to be byte-identical across instances; gating the reshape off when a given session's prefix looks cold would defeat the very sharing that is the entire benefit. The reshape is a one-time global cost, then shared. Leave them default-on.

## Caveat

Treatment's turn-1 was warm because earlier runs (the worktree probe) had already warmed its cwd-independent prefix — which is exactly the realistic steady state for ongoing use, but means this run does not measure a *cold-start* single session.
A cold-start treatment session would pay the one-time reshape write on turn 1 and then track the control nearly step-for-step (per-turn ~neutral) — i.e. it would land slightly behind and stay there, never amortizing, which is the same conclusion from the other direction: single-session value ≈ the small strip-sections trim only.
Carried (−4.5%) is the warmth-independent metric and is the honest single-session headline; the −22% $ is the cross-instance effect made visible.
