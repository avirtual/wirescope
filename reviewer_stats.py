#!/usr/bin/env python3
"""Descriptive statistics over the clodex-reviewer corpus rows emitted by
analyze_reviewer.py --json.

analyze_reviewer.py answers "would pre-serving pay". This answers "what does a
reviewer session actually LOOK like" -- distributions, spread and outliers, with
denominators, because the corpus is small enough (n=64) that a mean hides the
shape. Every session is one row; nothing here re-reads the captures.

Usage:  python3 reviewer_stats.py rev2.json
"""

import json
import statistics
import sys
from collections import Counter

READ, WRITE, OUT = 0.5e-6, 6.25e-6, 25e-6   # opus-5 USD/token
SHAPES = ("diff-file", "diff-inline", "git-bash", "no-diff")


def q(xs, p):
    """Percentile by nearest-rank on the sorted sample (no interpolation --
    with n=64 an interpolated 'p90' invents a session that does not exist)."""
    s = sorted(xs)
    if not s:
        return 0
    k = max(0, min(len(s) - 1, int(round(p / 100 * len(s) + 0.5)) - 1))
    return s[k]


def dist(name, xs, fmt="{:.0f}"):
    s = sorted(xs)
    n = len(s)
    f = lambda v: fmt.format(v)
    print(f"  {name:16} n={n:<4} min {f(s[0]):>9}  p25 {f(q(s,25)):>9}  "
          f"med {f(statistics.median(s)):>9}  p75 {f(q(s,75)):>9}  "
          f"p90 {f(q(s,90)):>9}  max {f(s[-1]):>9}  mean {f(statistics.mean(s)):>9}")


def share_of_total(name, xs, top=5):
    """Concentration: how much of the total do the top-k rows carry?"""
    s = sorted(xs, reverse=True)
    tot = sum(s)
    if tot <= 0:
        return
    print(f"  {name:16} top1 {s[0]/tot*100:4.1f}%   "
          f"top{top} {sum(s[:top])/tot*100:5.1f}%   "
          f"top half {sum(s[:len(s)//2])/tot*100:5.1f}%   "
          f"gini {gini(s):.2f}")


def gini(xs):
    s = sorted(xs)
    n, tot = len(s), sum(s)
    if not tot:
        return 0.0
    cum = sum((i + 1) * v for i, v in enumerate(s))
    return (2 * cum) / (n * tot) - (n + 1) / n


def rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):                    # average ranks over ties
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(xs, ys):
    a, b = rank(xs), rank(ys)
    n = len(a)
    ma, mb = statistics.mean(a), statistics.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else 0.0


def loglog_slope(xs, ys):
    import math
    pts = [(math.log(x), math.log(y)) for x, y in zip(xs, ys) if x > 0 and y > 0]
    mx = statistics.mean(p[0] for p in pts)
    my = statistics.mean(p[1] for p in pts)
    den = sum((p[0] - mx) ** 2 for p in pts)
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / den if den else 0.0


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "rev2.json"
    with open(path) as fh:
        rows = json.load(fh)

    n = len(rows)
    tot_cost = sum(r["cost_usd"] for r in rows)
    tot_rounds = sum(r["rounds"] for r in rows)
    tot_calls = sum(r["calls"] for r in rows)
    tot_read = sum(r["read_tokens"] for r in rows)
    tot_write = sum(r.get("write_tokens", 0) for r in rows)
    tot_out = sum(r.get("out_tokens", 0) for r in rows)

    print(f"=== corpus: {n} sessions, {tot_rounds} rounds, {tot_calls} calls, "
          f"${tot_cost:.2f} ===\n")

    print("--- per-session distributions ---")
    dist("rounds", [r["rounds"] for r in rows])
    dist("calls", [r["calls"] for r in rows])
    dist("calls/round", [r["calls"] / max(r["rounds"], 1) for r in rows], "{:.2f}")
    dist("cost $", [r["cost_usd"] for r in rows], "{:.2f}")
    dist("$/round", [r["cost_usd"] / max(r["rounds"], 1) for r in rows], "{:.3f}")
    dist("final window", [r["final_window"] for r in rows], "{:,.0f}")
    dist("mean window", [r["mean_window"] for r in rows], "{:,.0f}")
    dist("read tok", [r["read_tokens"] for r in rows], "{:,.0f}")
    dist("result chars", [r.get("result_chars", 0) for r in rows], "{:,.0f}")
    dist("solo-round %", [r["solo_rounds"] / max(r["rounds"], 1) * 100
                          for r in rows], "{:.0f}%")

    print("\n--- concentration (is the bill a few sessions?) ---")
    share_of_total("cost", [r["cost_usd"] for r in rows])
    share_of_total("rounds", [r["rounds"] for r in rows])
    share_of_total("read tok", [r["read_tokens"] for r in rows])

    print("\n--- where the money goes (token class x price) ---")
    for label, tok, price in (("read (cached+un)", tot_read, READ),
                              ("write", tot_write, WRITE),
                              ("output", tot_out, OUT)):
        usd = tok * price
        print(f"  {label:18} {tok:>12,} tok   ${usd:7.2f}   {usd/tot_cost*100:5.1f}%")

    print("\n--- window GROWTH within a session (receipt anchors) ---")
    firsts, lasts, growth, perround = [], [], [], []
    for r in rows:
        p = r.get("prefixes") or []
        if len(p) < 2:
            continue
        firsts.append(p[0])
        lasts.append(p[-1])
        growth.append(p[-1] / max(p[0], 1))
        perround.append((p[-1] - p[0]) / (len(p) - 1))
    dist("first window", firsts, "{:,.0f}")
    dist("last window", lasts, "{:,.0f}")
    dist("growth x", growth, "{:.1f}x")
    dist("tok added/round", perround, "{:,.0f}")
    print(f"  (n={len(growth)}/{n} sessions with >=2 priced rounds)")

    print("\n--- tool mix ---")
    tools = Counter()
    for r in rows:
        tools.update(r.get("tools") or {})
    for name, c in tools.most_common():
        sess = sum(1 for r in rows if (r.get("tools") or {}).get(name))
        print(f"  {name:12} {c:5} calls  {c/tot_calls*100:5.1f}%   "
              f"in {sess:2}/{n} sessions")

    print("\n--- roster: loaded vs used ---")
    ros = Counter(r["roster"] for r in rows)
    print("  roster sizes: " + ", ".join(f"{k} tools x{v}" for k, v in
                                         sorted(ros.items())))

    print("\n--- round WIDTH (calls issued per assistant turn) ---")
    widths = Counter()
    for r in rows:
        widths.update(r.get("round_sizes") or [])
    tr = sum(widths.values())
    for w in sorted(widths):
        print(f"  {w} call{'s' if w > 1 else ' '}/round: {widths[w]:5} rounds  "
              f"{widths[w]/tr*100:5.1f}%")

    print("\n--- failed / denied calls (rounds burned on a refusal) ---")
    fc = sum(r.get("failed_calls", 0) for r in rows)
    dc = sum(r.get("denied_calls", 0) for r in rows)
    fs = sum(1 for r in rows if r.get("failed_calls"))
    ds = sum(1 for r in rows if r.get("denied_calls"))
    print(f"  failed calls  {fc:5}/{tot_calls} ({fc/tot_calls*100:.1f}%) "
          f"in {fs}/{n} sessions")
    print(f"  of which 'no such tool available': {dc} in {ds}/{n} sessions")

    print("\n--- by shape ---")
    hdr = (f"  {'shape':13}{'n':>3}{'rds/s':>7}{'calls/s':>9}{'$/sess':>8}"
           f"{'med $':>8}{'window':>9}{'solo%':>7}{'off%':>7}")
    print(hdr)
    for sh in SHAPES:
        g = [r for r in rows if r["shape"] == sh]
        if not g:
            continue
        c = sum(r["calls"] for r in g)
        print(f"  {sh:13}{len(g):3}"
              f"{sum(r['rounds'] for r in g)/len(g):7.1f}"
              f"{c/len(g):9.1f}"
              f"{sum(r['cost_usd'] for r in g)/len(g):8.2f}"
              f"{statistics.median(r['cost_usd'] for r in g):8.2f}"
              f"{statistics.median(r['mean_window'] for r in g):9,.0f}"
              f"{sum(r['solo_rounds'] for r in g)/sum(r['rounds'] for r in g)*100:7.1f}"
              f"{sum(r['off_diff'] for r in g)/c*100:7.1f}")

    print("\n--- what predicts cost? (Spearman rank rho, n=%d) ---" % n)
    for label, key in (("rounds", lambda r: r["rounds"]),
                       ("calls", lambda r: r["calls"]),
                       ("final window", lambda r: r["final_window"]),
                       ("result chars", lambda r: r.get("result_chars", 0)),
                       ("output tokens", lambda r: r.get("out_tokens", 0)),
                       ("diff tokens", lambda r: r.get("diff_tokens", 0))):
        print(f"  cost vs {label:15} rho {spearman([key(r) for r in rows],
                                                   [r['cost_usd'] for r in rows]):+.2f}")
    # carriage is quadratic in round count: doubling rounds should ~4x the read
    print("\n  read-token growth vs rounds (log-log slope): "
          f"{loglog_slope([r['rounds'] for r in rows], [r['read_tokens'] for r in rows]):.2f}"
          "   (1.0 = linear, 2.0 = each round re-reads a window that itself grows)")

    print("\n--- outliers (top 8 by cost) ---")
    print(f"  {'session':10}{'shape':13}{'rds':>4}{'calls':>6}{'window':>9}"
          f"{'$':>7}{'$ share':>9}")
    for r in sorted(rows, key=lambda r: -r["cost_usd"])[:8]:
        print(f"  {r['session'][:8]:10}{r['shape']:13}{r['rounds']:4}"
              f"{r['calls']:6}{r['final_window']:9,}{r['cost_usd']:7.2f}"
              f"{r['cost_usd']/tot_cost*100:8.1f}%")

    print("\n--- levers ---")
    b = sum(r["batch_usd"] for r in rows)
    pn = sum(r["preserve"]["net_usd"] for r in rows)
    pw = sum(1 for r in rows if r["preserve"]["wins"])
    print(f"  L1 pre-serve : net ${pn:+.2f} ({pn/tot_cost*100:+.1f}%), "
          f"pays in {pw}/{n} sessions, "
          f"kills {sum(r['servable_rounds'] for r in rows)}/{tot_rounds} rounds")
    print(f"  L2 pair solos: ${b:.2f} ({b/tot_cost*100:.1f}%), "
          f"{sum(r['solo_rounds'] for r in rows)}/{tot_rounds} rounds are solo")
    dist("  L2 $/session", [r["batch_usd"] for r in rows], "{:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
