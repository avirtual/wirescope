#!/usr/bin/env python3
"""Does FRONT-LOADING whole files into a reviewer's prefix pay for itself?

Premise correction (Bogdan, 2026-08-15): Read's ~60k-char ceiling is a CLI client
policy -- a guardrail against an agent reading a random huge file -- not a wire,
payload or context limit. Files have been delivered whole as data in the system
prompt before. So "the file is too big to preload" is not a real constraint, and
the interesting question is purely economic.

That also retires the wrong metric. Cross-session overlap of the LINE SETS two
reviewers read only matters if you can ship slices. If you ship whole files, the
question is whether sessions want the same FILES -- and they do (67% of wb-wrap-ui
reviewers open session-manager.js).

WHAT A PRELOAD COSTS. A preloaded file rides the cached prefix, so it is not paid
once -- it is paid as RENT on every remaining round, at the cache-read rate, plus
one write. It pays iff

    P*read*(R-k) + P*write  <  sum(cost of the k round trips it removes)
                               + carriage relief on the results they'd have added

WHAT IT SAVES. With the whole file present, EVERY Read of it becomes unnecessary
-- not just the re-reads. That is the part my earlier round-trip floor understated:
it only counted repeats, because slices were all that could be served.

STRATEGIES (weakest to strongest information):
  fixed-K   the K hottest files fleet-wide, identical for every reviewer. Byte-
            identical across seats => ONE shared cached segment (the cross-instance
            sharing that CLAUDE.md says is where main-line transforms actually pay).
  diff      the files the session's own diff touches. Per-session, knowable at
            bootstrap from the artifact the reviewer is handed.
  oracle    exactly the files the session turned out to read. Not implementable --
            it is the CEILING. If the oracle loses, the idea is dead.

CACHE REGIME. fixed-K is reported both cold (every seat pays the write) and warm
(fleet-shared: reviewers spawn often enough that the segment stays live, so only
reads are paid). diff/oracle are per-session and always pay their own write.

Usage:  python3 simulate_preload.py [--days 3] [--repo-root DIR] [--k 1,3,5,10]
"""

import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from glob import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_review_tools import (DEFAULT_LOGS, REVIEW_TAG, Session, canon,
                                  discover, rel, seq_of)

# fable-5 USD/token (CLAUDE.md): $10/$50 per MTok, cache write 12.5, read 1.0
IN, OUT, CW, CR = 10e-6, 50e-6, 12.5e-6, 1.0e-6
CPT_MSG, CPT_SYS = 2.98, 3.11          # measured densities (proxylab/tokest.py)

DIFF_FILE = re.compile(r"^diff --git a/(\S+)", re.M)
LINENO = re.compile(r"^\s*\d+\t", re.M)
DEFAULT_ROOT = "/Users/bogdan/projects/tmux/wb-wrap-ui"


def usage_of(req_path):
    rp = req_path.replace(".request.json", ".response.json")
    try:
        with open(rp) as fh:
            return json.load(fh).get("usage") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def tok(u, key):
    """Usage fields can be present-but-null on the wire -- `or 0`, not a default."""
    return u.get(key) or 0


def cost_of(u):
    return (tok(u, "input_tokens") * IN
            + tok(u, "cache_read_input_tokens") * CR
            + tok(u, "cache_creation_input_tokens") * CW
            + tok(u, "output_tokens") * OUT)


def window_of(u):
    return (tok(u, "input_tokens") + tok(u, "cache_read_input_tokens")
            + tok(u, "cache_creation_input_tokens"))


class Sim:
    """One reviewer session, with its per-request receipts aligned to its rounds."""

    def __init__(self, sess, paths):
        self.s = sess
        # Tool-loop requests only: a body with no tools[] is the CLI's title
        # side-call. This must PARSE -- scanning the first N chars for `"tools"`
        # inverts the filter, because in a real request the key sits past a huge
        # system+messages block while the tiny side-call has it up front.
        self.reqs = []
        for p in paths:
            try:
                with open(p) as fh:
                    body = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            body = body.get("body") or body
            if not body.get("tools"):
                continue
            u = usage_of(p)
            if u:
                self.reqs.append((p, u))
        self.reqs.sort(key=lambda t: seq_of(t[0]))
        self.ok = len(self.reqs) >= 2

    @property
    def actual(self):
        return sum(cost_of(u) for _, u in self.reqs)

    @property
    def rounds(self):
        return self.s.rounds

    def result_tokens(self, rnd):
        """Tokens the round's tool_results added to every later window."""
        return sum(len(c.res) for c in self.s.calls if c.round == rnd) / CPT_MSG

    def eliminable(self, preload):
        """Rounds whose every call is a Read of a preloaded file -> the round
        trip never has to happen."""
        by_round = defaultdict(list)
        for c in self.s.calls:
            by_round[c.round].append(c)
        return [r for r, cs in by_round.items()
                if cs and all(c.name == "Read" and c.canon in preload for c in cs)]

    def simulate(self, preload, ptokens, warm=False):
        """-> (delta_usd, rounds_saved). Negative delta = preload is cheaper."""
        gone = set(self.eliminable(preload))
        if not self.reqs:
            return 0.0, 0
        # request i (0-based) is the one whose response emitted round i+1
        saved = 0.0
        for i, (_, u) in enumerate(self.reqs):
            if (i + 1) in gone:
                saved += cost_of(u)
        # relief: results of eliminated rounds no longer ride later windows
        for r in gone:
            later = max(0, len(self.reqs) - r)
            saved += self.result_tokens(r) * CR * later
        # rent: the preload rides every surviving round
        survivors = len(self.reqs) - len(gone)
        rent = ptokens * CR * max(0, survivors)
        rent += 0.0 if warm else ptokens * CW
        return rent - saved, len(gone)


def file_tokens(path, root, cache={}):
    """Whole-file token cost of preloading. Resolved against the live checkout --
    worktrees are transient, so sizes are as-of-now, not as-of-review."""
    if path in cache:
        return cache[path]
    r = rel(path, "wb-wrap-ui")
    cand = os.path.join(root, r)
    n = 0
    if os.path.isfile(cand):
        n = os.path.getsize(cand) / CPT_SYS
    cache[path] = n
    return n


def diff_files(sess, root):
    """Files the session's own diff touches (bootstrap-knowable)."""
    out = set()
    for c in sess.calls:
        if c.name != "Read" or "diff --git" not in c.res:
            continue
        for m in DIFF_FILE.finditer(LINENO.sub("", c.res)):
            p = os.path.join(root, m.group(1))
            if os.path.isfile(p):
                out.add(canon(p)[0])
    return out


def report(name, deltas, saves, rounds_tot, extra=""):
    if not deltas:
        print(f"  {name:26} (no sessions)")
        return
    net = sum(deltas)
    won = sum(1 for d in deltas if d < 0)
    print(f"  {name:26} net {net:+8.2f} USD over {len(deltas):3} sess"
          f" | rounds {sum(saves):4}/{rounds_tot} = {100*sum(saves)/rounds_tot:4.1f}%"
          f" | pays in {won:3}/{len(deltas):3} {extra}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=DEFAULT_LOGS)
    ap.add_argument("--days", type=float, default=3)
    ap.add_argument("--last", type=int,
                    help="only the N most recent reviewer seats")
    ap.add_argument("--repo-root", default=DEFAULT_ROOT)
    ap.add_argument("--k", default="1,3,5,10,20")
    args = ap.parse_args()

    groups = discover(args.logs, args.days, last=args.last)
    sims = []
    for (sid, tag), paths in groups.items():
        try:
            s = Session(sid, tag, paths)
        except Exception:
            continue
        if not s.ok:
            continue
        sim = Sim(s, paths)
        if sim.ok:
            sims.append(sim)

    tot_rounds = sum(s.rounds for s in sims)
    actual = sum(s.actual for s in sims)
    print(f"\n{'='*78}\nPRELOAD ECONOMICS — {len(sims)} reviewer sessions, "
          f"{args.days:g}d, fable-5 pricing\n{'='*78}")
    print(f"  measured spend {actual:7.2f} USD over {tot_rounds} rounds"
          f"  ({actual/len(sims):.3f}/session, {actual/tot_rounds:.4f}/round)")

    # hottest files fleet-wide
    freq = Counter()
    for s in sims:
        freq.update({c.canon for c in s.s.calls if c.name == "Read" and c.canon})
    hot = [f for f, _ in freq.most_common(40)
           if file_tokens(f, args.repo_root) > 0]

    print(f"\n-- ORACLE (ceiling: preload exactly what each session read) " + "-"*17)
    d, sv = [], []
    for s in sims:
        pre = {c.canon for c in s.s.calls if c.name == "Read" and c.canon}
        pt = sum(file_tokens(f, args.repo_root) for f in pre)
        delta, k = s.simulate(pre, pt)
        d.append(delta); sv.append(k)
    report("oracle (whole files)", d, sv, tot_rounds)
    pts = [sum(file_tokens(f, args.repo_root)
               for f in {c.canon for c in s.s.calls if c.name == "Read" and c.canon})
           for s in sims]
    print(f"  median preload {statistics.median(pts):,.0f} tok"
          f"  (p90 {sorted(pts)[int(.9*len(pts))]:,.0f})")

    print(f"\n-- DIFF-DERIVED (files the session's diff touches) " + "-"*27)
    d, sv, sizes = [], [], []
    for s in sims:
        pre = diff_files(s.s, args.repo_root)
        if not pre:
            continue
        pt = sum(file_tokens(f, args.repo_root) for f in pre)
        delta, k = s.simulate(pre, pt)
        d.append(delta); sv.append(k); sizes.append(pt)
    report("diff files (whole)", d, sv, tot_rounds)
    if sizes:
        print(f"  median preload {statistics.median(sizes):,.0f} tok"
              f"  ({len(sizes)} sessions had a parseable diff)")

    print(f"\n-- FIXED TOP-K (one shared block for every reviewer) " + "-"*25)
    for k in [int(x) for x in args.k.split(",")]:
        pre = set(hot[:k])
        pt = sum(file_tokens(f, args.repo_root) for f in pre)
        for warm, lab in ((False, "cold"), (True, "warm/shared")):
            d, sv = [], []
            for s in sims:
                delta, n = s.simulate(pre, pt, warm=warm)
                d.append(delta); sv.append(n)
            report(f"top-{k} ({pt/1000:.0f}k tok, {lab})", d, sv, tot_rounds)

    print(f"\n-- THE CEILING (why no preload can do better) " + "-"*32)
    comp = Counter()
    for s in sims:
        by_round = defaultdict(set)
        for c in s.s.calls:
            by_round[c.round].add(c.name)
        for names in by_round.values():
            if names == {"Read"}:
                comp["pure Read (preloadable)"] += 1
            elif "Read" in names:
                comp["Read + Grep/Glob (round still flies)"] += 1
            else:
                comp["no Read at all (preload irrelevant)"] += 1
    t = sum(comp.values())
    for k, v in comp.most_common():
        print(f"  {v:5} {100*v/t:5.1f}%  {k}")
    print("  A preload can only remove a round whose EVERY call it serves. Search")
    print("  is half the loop, so ~64% of rounds are out of reach by construction.")

    print(f"\n-- SIZE-CAPPED (preload a hot file only if it is SMALL) " + "-"*21)
    print("  The oracle loses because a whole 5.6k-line file is rented every round")
    print("  to serve the ~600 lines one session wants. So cap what may be sent:")
    for cap in (2000, 5000, 10000, 20000, 50000):
        pre_all = {f for f in hot if file_tokens(f, args.repo_root) <= cap}
        d, sv = [], []
        for s in sims:
            pre = {c.canon for c in s.s.calls
                   if c.name == "Read" and c.canon in pre_all}
            pt = sum(file_tokens(f, args.repo_root) for f in pre)
            delta, k = s.simulate(pre, pt)
            d.append(delta); sv.append(k)
        report(f"oracle, files <={cap//1000}k tok", d, sv, tot_rounds,
               f"({len(pre_all)} files eligible)")

    print(f"\n-- BREAK-EVEN (how big may a preload be before it costs more?) " + "-"*14)
    print("  Rent is ptokens*CR per surviving round; a saved round is worth its")
    print("  own receipt. Solving for ptokens at the median session:")
    med_rounds = statistics.median([len(s.reqs) for s in sims])
    med_round_cost = actual / tot_rounds
    for saved in (1, 2, 3, 5, 8):
        be = saved * med_round_cost / (CR * max(1, med_rounds - saved))
        print(f"    save {saved} round(s) of {med_rounds:.0f}  ->  preload must be "
              f"< {be:9,.0f} tok  ({be*CPT_SYS/1024:6,.0f} KB of source)")

    print(f"\n-- HOTTEST FILES (preload candidates) " + "-"*40)
    print(f"  {'sess':>5} {'%':>6} {'tokens':>9}  file")
    for f, c in freq.most_common(12):
        print(f"  {c:>5} {100*c/len(sims):5.1f}% {file_tokens(f, args.repo_root):>9,.0f}"
              f"  {rel(f, 'wb-wrap-ui')}")
    print()


if __name__ == "__main__":
    sys.exit(main())
