#!/usr/bin/env python3
"""Where does a reviewer seat's money actually go, and which lever moves the most?

Successor to the preload/pagination probes, both of which turned out to chase
small money. This decomposes the real bill per BILLING BUCKET and then prices each
candidate lever against it, so the levers are ranked by what they are worth rather
than by how easy they are to describe.

PRICING IS PER MODEL, read from proxylab.billing. Reviewer seats are a MIX
(opus-5 and fable-5, and fable is 2x opus), so a single flat rate misprices the
corpus -- an earlier pass of this analysis assumed fable throughout and overstated
the total by ~1.7x.

THINKING TOKENS ARE NOT IN response.json. They live in `output_tokens_details`
on the SSE terminal `message_delta`, so any accounting that reads only the JSON
receipt reports thinking as zero -- which is exactly wrong for these seats, where
thinking is the single largest line item. This script parses the SSE.

Levers priced here:
  raise-read-cap    CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS -- kills cap-forced
                    pagination round trips (measured in analyze_pagination.py)
  grep-context      `-C` on the Greps whose hit is later Read anyway
  effort            output_config effort=high on every seat; output is priced 5x
                    input, and thinking is most of it
  batch-rounds      the solo rounds that could have ridden with a neighbour

Each is reported as measured-today vs modelled-after, with the assumption stated.
No lever is claimed to compose with another; they overlap and are ranked alone.

Usage:  python3 reviewer_levers.py [--days 3]
"""

import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_review_tools import DEFAULT_LOGS, Session, discover
from analyze_pagination import chains_of
from simulate_preload import Sim, tok

from proxylab import billing

M = 1e-6


def price(model):
    p = billing.PRICES.get(model) or billing.PRICES.get("claude-opus-5")
    return (p["in"] * M, p["out"] * M, p["cache_write_5m"] * M, p["cache_read"] * M)


def sse_usage(req_path):
    """Terminal usage from the stream -- the ONLY place thinking_tokens appears."""
    p = req_path.replace(".request.json", ".response.sse")
    if not os.path.exists(p):
        return None
    last = None
    for ln in open(p, errors="ignore"):
        if not ln.startswith("data: "):
            continue
        try:
            d = json.loads(ln[6:])
        except json.JSONDecodeError:
            continue
        if d.get("type") == "message_delta" and d.get("usage"):
            last = d["usage"]
    return last


def model_of(req_path):
    try:
        with open(req_path) as fh:
            b = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return "claude-opus-5"
    return (b.get("body") or b).get("model") or "claude-opus-5"


class Round:
    __slots__ = ("model", "inp", "cr", "cw", "out", "think", "idx", "last")

    def __init__(self, model, u, su, idx, last):
        self.model, self.idx, self.last = model, idx, last
        self.inp = tok(u, "input_tokens")
        self.cr = tok(u, "cache_read_input_tokens")
        self.cw = tok(u, "cache_creation_input_tokens")
        self.out = tok(u, "output_tokens")
        d = (su or {}).get("output_tokens_details") or {}
        self.think = d.get("thinking_tokens") or 0

    def cost(self):
        i, o, w, r = price(self.model)
        return self.inp * i + self.cr * r + self.cw * w + self.out * o

    def bucket(self):
        i, o, w, r = price(self.model)
        return {"input": self.inp * i, "cache_read": self.cr * r,
                "cache_write": self.cw * w,
                "output_think": self.think * o,
                "output_real": (self.out - self.think) * o}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=DEFAULT_LOGS)
    ap.add_argument("--days", type=float, default=3)
    ap.add_argument("--last", type=int,
                    help="only the N most recent reviewer seats")
    args = ap.parse_args()

    groups = discover(args.logs, args.days, last=args.last)
    sessions, rounds_by_sess = [], []
    for k, paths in groups.items():
        try:
            s = Session(*k, paths)
        except Exception:
            continue
        if not s.ok:
            continue
        sim = Sim(s, paths)
        if not sim.ok:
            continue
        model = model_of(sim.reqs[-1][0])
        rs = [Round(model, u, sse_usage(p), i, i == len(sim.reqs) - 1)
              for i, (p, u) in enumerate(sim.reqs)]
        sessions.append((s, sim, model))
        rounds_by_sess.append(rs)

    allr = [r for rs in rounds_by_sess for r in rs]
    total = sum(r.cost() for r in allr)
    models = Counter(m for _, _, m in sessions)

    print(f"\n{'='*78}\nREVIEWER COST LEVERS — {len(sessions)} sessions, "
          f"{len(allr)} rounds, {args.days:g} days\n{'='*78}")
    print("  models: " + ", ".join(f"{m} x{c}" for m, c in models.most_common()))
    print(f"  TOTAL ${total:,.2f}   (${total/len(sessions):.2f}/session, "
          f"${total/len(allr):.3f}/round)")

    print("\n-- WHERE THE MONEY IS " + "-"*56)
    buck = Counter()
    for r in allr:
        buck.update(r.bucket())
    for k, v in buck.most_common():
        print(f"  {k:14} ${v:8,.2f}  {100*v/total:5.1f}%")
    print(f"  {'-'*40}")
    carriage = buck["input"] + buck["cache_read"] + buck["cache_write"]
    print(f"  {'carriage':14} ${carriage:8,.2f}  {100*carriage/total:5.1f}%"
          f"   (re-shipping the window)")
    print(f"  {'generation':14} ${buck['output_think']+buck['output_real']:8,.2f}"
          f"  {100*(buck['output_think']+buck['output_real'])/total:5.1f}%")

    # thinking split: loop vs deliverable
    loop_t = sum(r.bucket()["output_think"] for r in allr if not r.last)
    fin_t = sum(r.bucket()["output_think"] for r in allr if r.last)
    print(f"\n  thinking, LOOP rounds  ${loop_t:8,.2f}  {100*loop_t/total:5.1f}%"
          f"   <- reasoning that is thrown away after the tool call")
    print(f"  thinking, FINAL round  ${fin_t:8,.2f}  {100*fin_t/total:5.1f}%"
          f"   <- reasoning behind the verdict")

    print("\n-- LEVERS, RANKED " + "-"*60)

    # 1. effort / thinking
    print(f"  [1] THINKING BUDGET")
    print(f"      every seat runs output_config effort=high + adaptive thinking;")
    print(f"      output bills 5x input. Thinking = ${buck['output_think']:,.2f}"
          f" ({100*buck['output_think']/total:.1f}% of bill).")
    perround = [r.think for r in allr]
    print(f"      per round: med {statistics.median(perround):,.0f} tok,"
          f" p90 {sorted(perround)[int(.9*len(perround))]:,} tok,"
          f" {sum(1 for x in perround if x==0)} rounds at zero")
    for frac in (0.25, 0.5):
        print(f"      cut loop-round thinking by {frac*100:.0f}%"
              f"  ->  ${loop_t*frac:,.2f} ({100*loop_t*frac/total:.1f}% of bill)")
    print(f"      (lever: effort=medium for the LOOP, high for the verdict; needs")
    print(f"       an A/B on review QUALITY before trusting -- this is the one")
    print(f"       lever that can make reviews worse.)")

    # 2. round count -> carriage + loop output
    print(f"\n  [2] ROUND COUNT")
    nr = [len(rs) for rs in rounds_by_sess]
    print(f"      med {statistics.median(nr):.0f} rounds/session."
          f" Each round re-reads the window AND emits fresh thinking.")
    marg = []
    for rs in rounds_by_sess:
        if len(rs) > 2:
            marg.append(statistics.median([r.cost() for r in rs[1:-1]]))
    if marg:
        mm = statistics.median(marg)
        print(f"      median marginal round costs ${mm:.3f}"
              f"  ->  each round removed is worth that")
        for k in (1, 2, 3):
            print(f"      remove {k} round(s)/session: ${mm*k*len(sessions):,.2f}"
                  f"  ({100*mm*k*len(sessions)/total:.1f}% of bill)")

    # 3. grep -C
    print(f"\n  [3] GREP CONTEXT (-C)")
    gd = sum(s.grep_directed() for s, _, _ in sessions)
    nogrep_ctx = sum(1 for s, _, _ in sessions for c in s.calls
                     if c.name == "Grep" and not any(k in c.inp for k in ("-A", "-B", "-C")))
    print(f"      {gd} re-reads fetch a line a prior Grep already found;"
          f" {nogrep_ctx} Greps ship no -C.")
    solo_gd = 0
    for (s, _, _), rs in zip(sessions, rounds_by_sess):
        by_round = defaultdict(list)
        for c in s.calls:
            by_round[c.round].append(c)
        for c in s.calls:
            if (c.name == "Read" and c.kind in ("DISJOINT", "OVERLAP")
                    and len(by_round[c.round]) == 1):
                solo_gd += 1
        del by_round
    print(f"      upper bound if every such Read vanished: {solo_gd} solo rounds")
    if marg:
        print(f"      ~= ${solo_gd*statistics.median(marg):,.2f}"
              f" ({100*solo_gd*statistics.median(marg)/total:.1f}% of bill)")

    # 4. pagination
    print(f"\n  [4] READ CAP (the cheap one)")
    capf = 0
    for (s, _, _), rs in zip(sessions, rounds_by_sess):
        by_round = defaultdict(list)
        for c in s.calls:
            by_round[c.round].append(c)
        for ch in chains_of(s):
            for prev, x in zip(ch, ch[1:]):
                if not prev.inp.get("limit") and len(by_round[x.round]) == 1:
                    capf += 1
    print(f"      {capf} solo round trips are cap-forced pagination")
    if marg:
        print(f"      ~= ${capf*statistics.median(marg):,.2f}"
              f" ({100*capf*statistics.median(marg)/total:.1f}% of bill)")
    print(f"      one env var, no behaviour change, no quality risk")
    print()


if __name__ == "__main__":
    sys.exit(main())
