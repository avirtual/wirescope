#!/usr/bin/env python3
"""Which consecutive rounds could have been ONE round?

Lever 2 prices "pair up the solo rounds" but that is only cash if the calls were
INDEPENDENT -- if round k+1's arguments depend on round k's result, batching them
is impossible, not merely awkward. So classify each adjacent round pair:

  SAME_TOOL_INDEP  next round calls the same tool on a target that does NOT appear
                   in the previous round's result text. Nothing was learned from
                   the result that chose the target => it was already knowable =>
                   batchable. This is the honest lower bound.
  DEPENDENT        the next target's path/pattern appears IN the previous result
                   (the reviewer read a result and followed a reference), or the
                   previous call errored (a retry is causally forced).
  MIXED            different tools, target not in prior result. Read+Grep in one
                   round is legal, so these are batchable too, but they are a
                   weaker claim than SAME_TOOL_INDEP -- reported separately.

Only adjacent pairs where BOTH rounds are single-call are considered: those are
the ones lever 2 proposes to merge.

Usage:  python3 reviewer_batchable.py [--logs DIR] [--session ID]
"""

import argparse
import json
import os
import sys
from collections import Counter
from glob import glob

DEFAULT_LOGS = os.path.expanduser(
    "~/Library/Application Support/clodex/wirescope/logs"
)


def seq_of(p):
    return int(os.path.basename(p).split("-")[0])


def result_text(bl):
    c = bl.get("content")
    if isinstance(c, list):
        return "".join(x.get("text", "") for x in c if isinstance(x, dict))
    return c or ""


def target_of(tu):
    i = tu.get("input") or {}
    return (i.get("file_path") or i.get("path") or i.get("pattern")
            or i.get("command") or "")


def analyse(sid_dir):
    reqs = sorted(glob(os.path.join(sid_dir, "*reviewer*.request.json")),
                  key=seq_of)
    if not reqs:
        return None
    with open(reqs[-1]) as fh:
        b = json.load(fh)
    body = b.get("body") or b
    msgs = body.get("messages", [])

    results, errors = {}, {}
    for m in msgs:
        if isinstance(m.get("content"), list):
            for bl in m["content"]:
                if bl.get("type") == "tool_result":
                    results[bl.get("tool_use_id")] = result_text(bl)
                    errors[bl.get("tool_use_id")] = bool(bl.get("is_error"))

    rounds = []
    for m in msgs:
        if m.get("role") == "assistant" and isinstance(m.get("content"), list):
            tus = [x for x in m["content"] if x.get("type") == "tool_use"]
            if tus:
                rounds.append(tus)

    tally = Counter()
    for a, b2 in zip(rounds, rounds[1:]):
        if len(a) != 1 or len(b2) != 1:
            continue
        pa, pb = a[0], b2[0]
        tally["solo_pairs"] += 1
        prev = results.get(pa["id"], "")
        if errors.get(pa["id"]):
            tally["DEPENDENT"] += 1
            continue
        tgt = target_of(pb)
        base = os.path.basename(tgt) if "/" in tgt else tgt
        # did the previous RESULT name where we went next?
        if base and len(base) > 2 and base in prev:
            tally["DEPENDENT"] += 1
        elif pa["name"] == pb["name"]:
            tally["SAME_TOOL_INDEP"] += 1
        else:
            tally["MIXED"] += 1
    tally["rounds"] = len(rounds)
    return tally


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=DEFAULT_LOGS)
    ap.add_argument("--session")
    args = ap.parse_args()

    tot = Counter()
    n = 0
    for d in sorted(glob(os.path.join(args.logs, "*"))):
        sid = os.path.basename(d)
        if sid.startswith("_") or (args.session and not sid.startswith(args.session)):
            continue
        t = analyse(d)
        if not t:
            continue
        n += 1
        tot.update(t)

    sp = tot["solo_pairs"]
    if not sp:
        print("no adjacent solo pairs", file=sys.stderr)
        return 1
    print(f"=== {n} sessions, {tot['rounds']} rounds, "
          f"{sp} ADJACENT SOLO PAIRS (the merge candidates) ===\n")
    for k in ("SAME_TOOL_INDEP", "MIXED", "DEPENDENT"):
        print(f"  {k:18} {tot[k]:5}  {tot[k]/sp*100:5.1f}% of pairs")
    ok = tot["SAME_TOOL_INDEP"] + tot["MIXED"]
    print(f"\n  batchable (indep+mixed): {ok}/{sp} = {ok/sp*100:.1f}%")
    print(f"  causally forced        : {tot['DEPENDENT']}/{sp} = "
          f"{tot['DEPENDENT']/sp*100:.1f}%")
    print(f"\n  merging every batchable pair removes {ok} of {tot['rounds']} rounds "
          f"({ok/tot['rounds']*100:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
