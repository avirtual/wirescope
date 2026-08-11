#!/usr/bin/env python3
"""Is the reviewer's OUTPUT bill really irreducible?

Output is 35% of the corpus bill and the obvious answer is "that IS the review,
you cannot shrink the deliverable". True for the final diagnostic. But output
tokens are emitted on EVERY round, not just the last one, and the wire splits
them for us: usage_final.output_tokens_details.thinking_tokens. So:

    final diagnostic   = output of the last round (the deliverable -- untouchable)
    loop output        = output of every OTHER round = per-round thinking + the
                         tool_use JSON + any narration. This scales with round
                         COUNT, so it is on the same lever as reads and writes.

If loop output is a large share, then "we cannot optimize the output" is only
true of the last round, and cutting rounds cuts output too.

Usage:  python3 reviewer_output.py [--logs DIR] [--json OUT]
"""

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from glob import glob

READ, WRITE, OUT = 0.5e-6, 6.25e-6, 25e-6
DEFAULT_LOGS = os.path.expanduser(
    "~/Library/Application Support/clodex/wirescope/logs"
)


def seq_of(p):
    return int(os.path.basename(p).split("-")[0])


def rounds_of(sid_dir):
    """-> [(seq, out_tok, thinking_tok, stop_reason)] in wire order."""
    out = []
    for p in sorted(glob(os.path.join(sid_dir, "*reviewer*.response.json")),
                    key=seq_of):
        try:
            with open(p) as fh:
                j = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        u = (j.get("meta") or {}).get("usage_final") or j.get("usage") or {}
        if not u:
            continue
        o = u.get("output_tokens") or 0
        if not o:
            continue
        th = (u.get("output_tokens_details") or {}).get("thinking_tokens") or 0
        out.append((seq_of(p), o, th,
                    (j.get("meta") or {}).get("stop_reason")))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=DEFAULT_LOGS)
    ap.add_argument("--json")
    args = ap.parse_args()

    dirs = defaultdict(list)
    for p in glob(os.path.join(args.logs, "*", "*reviewer*.response.json")):
        sid = os.path.basename(os.path.dirname(p))
        if not sid.startswith("_"):
            dirs[sid].append(p)

    rows = []
    for sid in dirs:
        rs = rounds_of(os.path.join(args.logs, sid))
        if len(rs) < 2:
            continue
        # the deliverable is the last round that ended the turn rather than
        # calling a tool; fall back to the last round we have
        final = next((r for r in reversed(rs) if r[3] == "end_turn"), rs[-1])
        loop = [r for r in rs if r is not final]
        rows.append({
            "session": sid,
            "rounds": len(rs),
            "total_out": sum(r[1] for r in rs),
            "total_think": sum(r[2] for r in rs),
            "final_out": final[1],
            "final_think": final[2],
            "loop_out": sum(r[1] for r in loop),
            "loop_think": sum(r[2] for r in loop),
            "loop_rounds": len(loop),
        })

    if not rows:
        print("no sessions", file=sys.stderr)
        return 1

    n = len(rows)
    T = lambda k: sum(r[k] for r in rows)
    tot = T("total_out")

    print(f"=== output anatomy: {n} sessions, {T('rounds')} priced rounds, "
          f"{tot:,} output tok = ${tot*OUT:.2f} ===\n")

    print(f"  {'component':26}{'tokens':>12}{'$':>9}{'% of output':>13}")
    for label, k in (("FINAL diagnostic (text)", None),
                     ("  of which thinking", "final_think"),
                     ("LOOP rounds (all)", "loop_out"),
                     ("  of which thinking", "loop_think")):
        if k is None:
            v = T("final_out")
        else:
            v = T(k)
        print(f"  {label:26}{v:12,}{v*OUT:9.2f}{v/tot*100:12.1f}%")

    ft, lt = T("final_out"), T("loop_out")
    print(f"\n  final diagnostic text only: {ft - T('final_think'):,} tok "
          f"= ${(ft-T('final_think'))*OUT:.2f} "
          f"({(ft-T('final_think'))/tot*100:.1f}% of output)")
    print(f"  ROUND-COUPLED output (loop): {lt:,} tok = ${lt*OUT:.2f} "
          f"({lt/tot*100:.1f}% of output)")
    print(f"  all thinking, both places  : {T('total_think'):,} tok "
          f"= ${T('total_think')*OUT:.2f} ({T('total_think')/tot*100:.1f}%)")

    per = [r["loop_out"] / max(r["loop_rounds"], 1) for r in rows]
    print(f"\n  per LOOP round: median {statistics.median(per):,.0f} out tok "
          f"(mean {statistics.mean(per):,.0f})")
    pth = [r["loop_think"] / max(r["loop_rounds"], 1) for r in rows]
    print(f"  per LOOP round thinking: median {statistics.median(pth):,.0f} tok")
    print(f"  => each round trip removed also saves ~{statistics.median(per):,.0f} "
          f"output tok = ${statistics.median(per)*OUT:.4f}")

    fs = [r["final_out"] for r in rows]
    print(f"\n  final diagnostic size: median {statistics.median(fs):,.0f} tok, "
          f"min {min(fs):,}, max {max(fs):,}")
    share = [r["loop_out"] / r["total_out"] * 100 for r in rows]
    print(f"  loop share of a session's output: median {statistics.median(share):.0f}%, "
          f"min {min(share):.0f}%, max {max(share):.0f}%")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rows, fh, indent=1)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
