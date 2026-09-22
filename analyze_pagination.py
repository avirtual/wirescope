#!/usr/bin/env python3
"""How many reviewer round trips exist ONLY because a Read came back in pieces?

Premise (Bogdan, 2026-08-15): Read's ceiling is client policy, not a wire limit.
`CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` raises the token cap and an explicit
`limit=` overrides the 2000-line default, so a file that arrives in N chunks can
arrive in one. This prices that: every chunk after the first is a round trip, and
a round trip re-carries the whole window.

TWO CAUSES, TWO DIFFERENT FIXES -- the split is the point of this script:

  CAP-FORCED    the read asked for the whole file (no `limit`) and the tool cut it
                anyway. The agent had no say. FIX: raise the env cap.
  SELF-CHUNKED  the read asked for a small `limit` (70 lines, say) and got exactly
                that, then came back for more. The cap was never the constraint;
                the agent chose the window size. FIX: prompt/tooling guidance, NOT
                the env var -- raising the cap changes nothing here.

Conflating the two would credit an env change with savings only a prompt change
can deliver.

MEASURING THE CAP. Its value is not documented on the wire, so it is inferred:
no-limit reads that were provably cut (a later read resumed at end+1) cluster just
under the ceiling, and the largest no-limit read that was NOT cut sits just over
the largest that was. That brackets it.

A CHUNK CHAIN is a maximal run of reads of one file that tile contiguously
(next.start ~= prev.end+1). A chain of n costs n-1 extra reads. Whether those cost
whole ROUND TRIPS depends on what else rode the round: a chunk alone in its round
is a full extra trip; a chunk sharing a round with other work is not (that round
flew regardless), so it is counted separately rather than claimed.

Usage:  python3 analyze_pagination.py [--days 3] [--session ID]
"""

import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_review_tools import DEFAULT_LOGS, Session, discover, rel
from simulate_preload import Sim, cost_of

CPT_CODE = 2.5          # source-code density; see CAP inference below
DEFAULT_LINE_LIMIT = 2000


def chains_of(sess):
    """-> [[Call, ...]] maximal contiguous read runs, per file, in call order."""
    per = defaultdict(list)
    for c in sess.calls:
        if c.name == "Read" and c.canon and c.start and c.end:
            per[c.canon].append(c)
    out = []
    for f, cs in per.items():
        cur = [cs[0]]
        for prev, nxt in zip(cs, cs[1:]):
            # tiles onto the END of the run so far (not merely onto its last call)
            if any(0 <= nxt.start - x.end <= 2 for x in cur if x.end):
                cur.append(nxt)
            else:
                out.append(cur)
                cur = [nxt]
        out.append(cur)
    return [c for c in out if len(c) > 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=DEFAULT_LOGS)
    ap.add_argument("--days", type=float, default=3)
    ap.add_argument("--last", type=int,
                    help="only the N most recent reviewer seats")
    ap.add_argument("--session")
    args = ap.parse_args()

    groups = discover(args.logs, args.days, args.session, last=args.last)
    sims, sessions = [], []
    for k, paths in groups.items():
        try:
            s = Session(*k, paths)
        except Exception:
            continue
        if not s.ok:
            continue
        sessions.append(s)
        sims.append(Sim(s, paths))

    all_reads = [c for s in sessions for c in s.calls if c.name == "Read"]
    n_sess = len(sessions)
    tot_rounds = sum(s.rounds for s in sessions)
    print(f"\n{'='*78}\nREAD PAGINATION — {n_sess} reviewer sessions, {args.days:g} days"
          f"  ({len(all_reads)} Reads, {tot_rounds} rounds)\n{'='*78}")

    # ---- 1. infer the cap -------------------------------------------------
    chains = [c for s in sessions for c in chains_of(s)]
    cut = [x for ch in chains for x in ch[:-1]]              # every non-final chunk
    cap_forced = [x for x in cut if not x.inp.get("limit")]
    self_chunk = [x for x in cut if x.inp.get("limit")]
    nolimit_all = [c for c in all_reads if not c.inp.get("limit")
                   and not c.inp.get("offset") and c.res]
    uncut = [c for c in nolimit_all if c not in cap_forced]

    print("\n-- 1. WHERE IS THE CEILING " + "-"*50)
    if cap_forced:
        cc = sorted(len(x.res) for x in cap_forced)
        print(f"  no-limit reads PROVEN cut (a later read resumed at end+1):"
              f" {len(cc)}")
        print(f"    their sizes: {min(cc):,} .. {max(cc):,} chars")
    if uncut:
        uc = sorted(len(c.res) for c in uncut)
        print(f"  largest no-limit read NOT followed by a continuation:"
              f" {max(uc):,} chars")
    print("  NOTE: these two ranges OVERLAP, so chars do not locate the cap -- it")
    print("  is a TOKEN cap and density varies by file. A read can also be short")
    print("  simply because the file was short, and 'not continued' only means the")
    print("  agent moved on. Comparing against the live file does not settle it")
    print("  either: worktrees are transient and the file has since changed.")
    print("  Documented default is 25,000 tok (CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS);")
    print("  the wire is consistent with it but does not pin the value.")
    hit2k = [c for c in nolimit_all if c.end and c.start
             and c.end - c.start + 1 >= DEFAULT_LINE_LIMIT]
    print(f"  reads that hit the 2000-LINE default instead: {len(hit2k)}"
          f"  (the token cap binds first)")

    # ---- 2. the chains ----------------------------------------------------
    print("\n-- 2. CHUNK CHAINS (a file that arrived in pieces) " + "-"*27)
    lens = Counter(len(ch) for ch in chains)
    extra = sum(len(ch) - 1 for ch in chains)
    print(f"  chains                 : {len(chains)} across "
          f"{len({id(s) for s in sessions for ch in chains_of(s)})} sessions")
    print(f"  chain length           : " +
          "  ".join(f"{k} chunks x{v}" for k, v in sorted(lens.items())))
    print(f"  EXTRA reads (chunk 2+) : {extra}"
          f"   = {100*extra/len(all_reads):.1f}% of all Reads")
    print(f"    of which CAP-FORCED  : {len(cap_forced):3}  (no limit asked; tool cut it)"
          f"  -> raising the env cap fixes these")
    print(f"    of which SELF-CHUNKED: {len(self_chunk):3}  (agent asked for a small"
          f" window) -> prompt lever, NOT the env var")
    if self_chunk:
        askd = [x.inp["limit"] for x in self_chunk if isinstance(x.inp.get("limit"), int)]
        if askd:
            print(f"      limits the agent chose: med {statistics.median(askd):.0f} lines"
                  f"  (min {min(askd)}, max {max(askd)})")

    # ---- 3. do they cost whole round trips? -------------------------------
    print("\n-- 3. DID THE EXTRA CHUNK COST A ROUND TRIP? " + "-"*33)
    solo, shared = Counter(), Counter()
    solo_rounds = []          # (session_index, round, cause)
    for si, s in enumerate(sessions):
        by_round = defaultdict(list)
        for c in s.calls:
            by_round[c.round].append(c)
        for ch in chains_of(s):
            # the cause is the PRECEDING chunk's shape: it is the read that got
            # cut (or self-limited), not the one that resumed
            for prev, x in zip(ch, ch[1:]):
                cause = "self-chunked" if prev.inp.get("limit") else "cap-forced"
                if len(by_round[x.round]) == 1:
                    solo[cause] += 1
                    solo_rounds.append((si, x.round, cause))
                else:
                    shared[cause] += 1
    print(f"  chunk was the ONLY call in its round  : {sum(solo.values()):3}"
          f"  <- a whole API round trip, removable")
    for k in ("cap-forced", "self-chunked"):
        print(f"      {k:14}: {solo[k]:3}")
    print(f"  chunk shared the round with other work: {sum(shared.values()):3}"
          f"  <- round flew anyway, not claimable")
    for k in ("cap-forced", "self-chunked"):
        print(f"      {k:14}: {shared[k]:3}")

    # price the solo ones from receipts
    spend = Counter()
    tot = sum(sim.actual for sim in sims)
    for si, rnd, cause in solo_rounds:
        sim = sims[si]
        if rnd - 1 < len(sim.reqs):
            spend[cause] += cost_of(sim.reqs[rnd - 1][1])
    s_tot = sum(spend.values())
    print(f"\n  cost of those {sum(solo.values())} round trips : ${s_tot:,.2f}"
          f"  of ${tot:,.2f} total  ({100*s_tot/tot if tot else 0:.1f}%)")
    print(f"      raising the env cap would reclaim : ${spend['cap-forced']:,.2f}")
    print(f"      needs a PROMPT change instead     : ${spend['self-chunked']:,.2f}")
    print(f"  rounds removable              : {sum(solo.values())}/{tot_rounds}"
          f"  ({100*sum(solo.values())/tot_rounds:.1f}% of all rounds)")

    # ---- 4. which files ---------------------------------------------------
    print("\n-- 4. WHICH FILES ARRIVE IN PIECES " + "-"*43)
    f = Counter()
    for s in sessions:
        for ch in chains_of(s):
            f[ch[0].canon] += len(ch) - 1
    print(f"  {'extra':>6}  file")
    for path, n in f.most_common(10):
        print(f"  {n:>6}  {rel(path, 'wb-wrap-ui')}")
    print()


if __name__ == "__main__":
    sys.exit(main())
