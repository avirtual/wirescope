"""Long-session amortization trajectory: cumulative cost per arm, turn by turn.

Isolates the MAIN-LINE transforms (relocate-env / strip-sections / sort-tools /
claudemd-pathstamp) over one long single-cwd session — no subagents, no
worktrees. The open question (see ../README.md): the treatment pays a one-time
cold reshape of its prefix on turn 1; does that amortize as the session grows
(reshaped prefix then read warm every turn), or does it stay net-negative —
which would mean the main-line transforms' value is PURELY cross-instance
(proven separately in ../worktree-sharing/), not single-session?

Reads each arm's ONE session dir, pairs every main-line request (tools>0, skips
the tools=0 title side-call) with its response, orders by seq, and prints the
per-step and CUMULATIVE est_$ + carried tokens for both arms side by side, plus
the running treatment-minus-control delta and the crossover step (if any).

Usage:
  python3 trajectory.py --treat-dir D --ctrl-dir D --treat-sid SID --ctrl-sid SID
"""
import argparse
import json
import re
from pathlib import Path

SEQ_RE = re.compile(r"(\d+)")


def _steps(session_dir):
    """Ordered main-line (request, response) token+cost steps for one session."""
    out = []
    for req in sorted(Path(session_dir).glob("*.request.json"),
                      key=lambda p: int(SEQ_RE.match(p.name).group(1)) if SEQ_RE.match(p.name) else 0):
        body = json.load(open(req)).get("body", {})
        if len(body.get("tools", []) or []) <= 0:
            continue                              # title side-call / non-agent
        resp = Path(str(req).replace(".request.json", ".response.json"))
        if not resp.exists():
            continue
        r = json.load(open(resp))
        bil = r.get("billing", {})
        tok = bil.get("tokens", {}) or {}
        cr = tok.get("cache_read_input_tokens", 0) or 0
        cw = ((tok.get("cache_write_1h_tokens", 0) or 0)
              + (tok.get("cache_write_5m_tokens", 0) or 0)
              + (tok.get("cache_write_flat_tokens", 0) or 0))
        ui = tok.get("input_tokens", 0) or 0
        op = tok.get("output_tokens", 0) or 0
        out.append({
            "usd": bil.get("est_usd", 0.0) or 0.0,
            "carried": ui + cr + cw,
            "in": ui, "read": cr, "write": cw, "out": op,
        })
    return out


def main():
    ap = argparse.ArgumentParser(description="long-session amortization trajectory")
    ap.add_argument("--treat-dir", required=True)
    ap.add_argument("--ctrl-dir", required=True)
    ap.add_argument("--treat-sid", required=True)
    ap.add_argument("--ctrl-sid", required=True)
    args = ap.parse_args()

    T = _steps(f"{args.treat_dir}/{args.treat_sid}")
    C = _steps(f"{args.ctrl_dir}/{args.ctrl_sid}")
    n = min(len(T), len(C))
    if n == 0:
        raise SystemExit("no main-line steps found — check the session ids / dirs")
    if len(T) != len(C):
        print(f"# NOTE: step counts differ (treat {len(T)} vs ctrl {len(C)}); "
              f"comparing the first {n} (agent tool-paths can diverge).")

    print(f"\n  {'step':>4}  {'TREAT $':>9} {'cum':>9}   {'CTRL $':>9} {'cum':>9}   "
          f"{'cumΔ$':>9} {'cumΔ%':>7}   {'T.read':>7} {'T.write':>8} {'C.read':>7} {'C.write':>8}")
    print("  " + "-" * 104)
    tc = cc = 0.0
    tcar = ccar = 0
    cross = None
    for i in range(n):
        t, c = T[i], C[i]
        tc += t["usd"]; cc += c["usd"]
        tcar += t["carried"]; ccar += c["carried"]
        dd = tc - cc
        pct = (dd / cc * 100) if cc else 0.0
        if cross is None and i > 0 and dd <= 0:
            cross = i + 1
        print(f"  {i+1:>4}  {t['usd']:>9.5f} {tc:>9.5f}   {c['usd']:>9.5f} {cc:>9.5f}   "
              f"{dd:>+9.5f} {pct:>+6.1f}%   {t['read']:>7} {t['write']:>8} {c['read']:>7} {c['write']:>8}")

    print("\n# SUMMARY (first %d steps)" % n)
    print(f"  cumulative est_$ : TREAT ${tc:.5f}  vs  CTRL ${cc:.5f}   "
          f"(Δ ${tc-cc:+.5f}, {(tc-cc)/cc*100:+.1f}%)")
    print(f"  cumulative carried: TREAT {tcar:,}  vs  CTRL {ccar:,}   "
          f"(Δ {tcar-ccar:+,}, {(tcar-ccar)/ccar*100:+.1f}%)")
    if tc <= cc:
        print(f"  -> treatment is CHEAPER cumulatively over {n} steps"
              + (f" (crossed ahead at step {cross})" if cross else ""))
    elif cross:
        print(f"  -> treatment crossed ahead at step {cross} but the displayed "
              f"window ends behind — extend the session")
    else:
        print(f"  -> treatment stays MORE EXPENSIVE through {n} steps "
              f"(one-time reshape not amortized; cwd stable → no cross-instance gain here)")
    print("  (carried is the warmth-independent metric; $ depends on each prefix's warmth history.)")


if __name__ == "__main__":
    main()
