#!/usr/bin/env python3
"""Roll analyze_infoloads.py output into the per-shape ledger + counterfactual.

Counterfactual: the seat spawns a cold subagent with a ~1.5 KB question, the sub
does the same reads at ITS rate, and returns a summary the size of the cited
bytes, which the seat then carries for the same number of later requests. The
sub's run cost is CALIBRATED from real subagent runs in the same corpus (median
$/tool-call and $/MB ingested by model), not assumed; Haiku is priced by scaling
Sonnet's per-token rates, since no Haiku sub ran here.

Usage: python3 report_infoloads.py IN.json [--carry-rate USD_PER_MB_PER_REQ]
"""
import argparse
import json
import statistics
from collections import defaultdict

# ---- subagent cost: MECHANISTIC, validated against the real runs ------------
# A two-term `floor + slope*MB` fit was tried first and cannot be used to price
# a STRIPPED sub: fitted on the 85 real Agent-tool runs its floor collapses to
# ~ -$0.04, because prompt cost and carriage cost are collinear across runs, so
# the term clodex wants to re-price is exactly the term the fit cannot see.
#
# So the sub is priced from its own mechanics instead, in tokens:
#   P = prompt (system + tool schemas + the question) -- the term a stripped sub
#       changes, and the ONLY term it changes;
#   I = bytes it ingests / 2.98;  G = what it generates;  T = its turns.
#   write = (P + I + G) x write_rate            (each byte enters history once)
#   read  = (T*P + (I+G)*T/2) x read_rate       (prefix every turn, history on average half)
#   out   = G x out_rate
# Validated on all 85 runs: predicted/actual median 0.81 on BOTH models, p25-p75
# 0.77-0.88 -- a tight, unbiased-in-shape 19% under-count (cache rewrites and
# mid-turn busts the idealised model omits), so it is corrected by /0.81 and the
# correction is declared rather than buried.
MECH_CORRECTION = 1 / 0.81

RATES = {   # USD per token: cache write 5m, cache read, output
    "sonnet": {"w": 2.50e-6, "r": 0.20e-6, "o": 10.0e-6},
    "opus":   {"w": 6.25e-6, "r": 0.50e-6, "o": 25.0e-6},
    "haiku":  {"w": 1.25e-6, "r": 0.10e-6, "o": 5.0e-6},
}
# Prompt sizes measured off the 85 runs: tool schemas 24,454 ch for the stock
# 6-9 tool roster, of which Read+Grep+Glob+Bash = 9,089 ch; system 3,399 ch
# (the file-search agent prompt, already lean -- there is no CLAUDE.md in a sub
# prompt to omit, see the report); question ~2,573 B.
PROMPT = {
    "stock":    (24454 / 2.71) + (3399 / 2.71) + (2573 / 2.98),   # ~11.1k tok
    "stripped": (9089 / 2.71) + (3399 / 2.71) + (1500 / 2.98),    # ~5.1k tok
}
# Behaviour, fitted on the 85 runs: T = 0.3 + 0.308 x KB (corr 0.83), ~450
# output tok/turn.
#
# T_MIN IS THE LOAD-BEARING ASSUMPTION AND IT IS NOT MEASURED. The smallest
# real sub in the corpus ingests 29.8 KB; the median EPISODE is ~1 KB, so every
# episode-level number extrapolates ~30x below any observation. The linear fit
# would give such a sub 0.6 turns, which is not a thing -- a sub must at minimum
# read the question, act, and answer. T_MIN sets that floor and the report
# sweeps it rather than picking one.
TURNS_PER_KB = 0.308
T_MIN = 3.0
GEN_PER_TURN = 450.0


def sub_cost(model, ing_bytes, prompt="stock", t_min=None):
    """USD for one subagent run that ingests `ing_bytes`, from its mechanics."""
    R = RATES[model]
    P = PROMPT[prompt]
    I = ing_bytes / 2.98
    T = max(T_MIN if t_min is None else t_min,
            0.3 + (ing_bytes / 1024.0) * TURNS_PER_KB)
    G = T * GEN_PER_TURN
    usd = ((P + I + G) * R["w"]
           + (T * P + (I + G) * T / 2.0) * R["r"]
           + G * R["o"])
    return usd * MECH_CORRECTION

CODE_SHAPES = {"read source file(s)", "code search (grep/glob)"}
LOOKUP_MAX_CITED_LINES = 10


def cost(r):
    return r["res_usd"] + r["call_carry_usd"] + r["call_gen_usd"]


def sub_shape(r):
    """Addendum split: deterministic-lookup vs synthesis, for code reads only."""
    if r["shape"] not in CODE_SHAPES:
        return None
    return "lookup" if r["cited_lines"] <= LOOKUP_MAX_CITED_LINES else "synthesis"


def med(xs):
    xs = list(xs)
    return statistics.median(xs) if xs else 0.0


def carry_usd_per_byte(rows):
    """Measured marginal $ of carrying one result byte for one extra request."""
    num = sum(r["res_usd"] for r in rows if r["res_req"] > 0)
    den = sum(r["res_bytes"] * r["res_req"] for r in rows if r["res_req"] > 0)
    return num / den if den else 0.0


def episodes(rs, gap=4):
    """Group consecutive loads of one shape into EPISODES -- what one delegation
    would actually cover. Real leads do not spawn a sub per Read; they hand over
    a cluster of reads that sit next to each other in the turn stream. Two loads
    join an episode when fewer than `gap` blocks separate them."""
    eps = []
    for r in sorted(rs, key=lambda r: (r.get("session", ""), r["order"])):
        if eps and r.get("session") == eps[-1][-1].get("session") \
                and r["order"] - eps[-1][-1]["order"] <= gap:
            eps[-1].append(r)
        else:
            eps.append([r])
    return eps


# Compact cadence, measured on these 5 sessions: 63.16 MB of distinct message
# bytes entered the lead's windows and 213 compacts fired = 0.297 MB per
# compact, at $0.527 each. Delegation shrinks ingress (the seat carries a
# summary, not the read), so it BUYS BACK compacts at that rate -- clodex's
# missing term (1).
MB_PER_COMPACT = 0.2965
USD_PER_COMPACT = 0.5268


def ep_cost(e, cpb, model, prompt="stock", compact_credit=True):
    """(today_usd, sub_usd) for ONE episode under ONE sub model."""
    today = sum(cost(r) for r in e)
    ing = sum(r["res_bytes"] for r in e)
    summ_b = max(sum(r["cited_bytes"] for r in e), 200)   # floor: a verdict line
    req = max(r["res_req"] for r in e)
    sub = sub_cost(model, ing, prompt) + summ_b * req * cpb
    if compact_credit:
        # bytes this episode keeps OUT of the window buy back compacts pro rata
        today += (ing / 1e6) / MB_PER_COMPACT * USD_PER_COMPACT
        sub += (summ_b / 1e6) / MB_PER_COMPACT * USD_PER_COMPACT
    return today, sub


def counterfactual(rs, cpb, model, gap=4, prompt="stock"):
    """(sub_run_usd, carried_summary_usd, breakeven_requests, today_usd)."""
    eps = episodes(rs, gap)
    run = sum(sub_cost(model, sum(r["res_bytes"] for r in e), prompt) for e in eps)
    summ_b = sum(max(sum(r["cited_bytes"] for r in e), 200) for e in eps)
    req = med(r["res_req"] for r in rs)
    carried = summ_b * req * cpb
    today = sum(cost(r) for r in rs)
    # BREAK-EVEN is per episode: how many later requests must re-ship the median
    # episode's result before delegating it would have been the cheaper call.
    # Includes the compact credit on both sides.
    ib_ep = med(sum(r["res_bytes"] for r in e) for e in eps)
    sb_ep = med(max(sum(r["cited_bytes"] for r in e), 200) for e in eps)
    ccred = ((ib_ep - sb_ep) / 1e6) / MB_PER_COMPACT * USD_PER_COMPACT
    gain = (ib_ep - sb_ep) * cpb
    be = ((sub_cost(model, ib_ep, prompt) - ccred) / gain) if gain > 0 else float("inf")
    return run, carried, max(be, 0.0), today


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    a = ap.parse_args()
    data = json.loads(open(a.infile).read())

    allrows = []
    for s in data:
        for r in s["rows"]:
            r["session"] = s["session"]
            allrows.append((s["session"], r))
    info = [r for _, r in allrows if r["info"]]
    act = [r for _, r in allrows if not r["info"]]
    cpb = carry_usd_per_byte(info)

    tot = sum(s["usd_total"] for s in data)
    print(f"== {len(data)} sessions, receipt ${tot:,.2f}, "
          f"{sum(s['n_main'] for s in data):,} main-line requests")
    print(f"   tool calls {len(allrows):,}  info loads {len(info):,} "
          f"({100*len(info)/max(len(allrows),1):.0f}%)")
    ui, ua = sum(map(cost, info)), sum(map(cost, act))
    print(f"   info-load $ {ui:,.2f} ({100*ui/tot:.1f}% of spend)   "
          f"action-call $ {ua:,.2f} ({100*ua/tot:.1f}%)   "
          f"rest (prefix+prose+thinking) ${tot-ui-ua:,.2f}")
    print(f"   measured carry rate: ${cpb*1e6:.4f} per MB per extra request\n")

    print("-- per session")
    for s in data:
        i = [r for r in s["rows"] if r["info"]]
        u = sum(map(cost, i))
        print(f"   {s['session'][:8]}  req {s['n_main']:5,}  ${s['usd_total']:8,.2f}  "
              f"loads {len(i):4,}  info ${u:7,.2f} ({100*u/max(s['usd_total'],1e-9):4.1f}%)  "
              f"ingested {sum(r['res_bytes'] for r in i)/1e6:5.1f} MB  "
              f"cited {sum(r['cited_bytes'] for r in i)/1e6:4.1f} MB")

    by = defaultdict(list)
    for r in info:
        ss = sub_shape(r)
        by[r["shape"] + (f" [{ss}]" if ss else "")].append(r)

    print(f"\n-- per shape (med_in/med_cited = bytes per load; carried = median "
          f"later requests that re-shipped the result)")
    hdr = (f"{'shape':44s} {'n':>5s} {'ep':>4s} {'usd':>8s} {'%':>5s} {'med_in':>8s} "
           f"{'med_cit':>8s} {'cit%':>5s} {'carr':>5s} {'sonnet':>8s} {'haiku':>8s} {'be':>6s}")
    print(hdr)
    for sh, rs in sorted(by.items(), key=lambda kv: -sum(map(cost, kv[1]))):
        u = sum(map(cost, rs))
        run, carried, be, today = counterfactual(rs, cpb, "sonnet")
        hrun, hcar, _, _ = counterfactual(rs, cpb, "haiku")
        ib = sum(r["res_bytes"] for r in rs)
        cb = sum(r["cited_bytes"] for r in rs)
        ne = len(episodes(rs))
        print(f"{sh:44s} {len(rs):5d} {ne:4d} {u:8.2f} {100*u/ui:4.1f}% "
              f"{med(r['res_bytes'] for r in rs):8.0f} {med(r['cited_bytes'] for r in rs):8.0f} "
              f"{100*cb/max(ib,1):4.0f}% {med(r['res_req'] for r in rs):5.0f} "
              f"{run+carried:8.2f} {hrun+hcar:8.2f} {(be if be < 1e6 else float('inf')):6.0f}")

    print(f"\n-- counterfactual totals (all info loads, by sub model)")
    for m in ("sonnet", "opus", "haiku"):
        run = carried = 0.0
        for sh, rs in by.items():
            r1, c1, _, _ = counterfactual(rs, cpb, m)
            run += r1
            carried += c1
        print(f"   {m:7s} sub-run ${run:8,.2f} + carried summary ${carried:7,.2f} "
              f"= ${run+carried:8,.2f}   vs today ${ui:,.2f}   "
              f"delta {'SAVES' if run+carried < ui else 'COSTS'} "
              f"${abs(ui-(run+carried)):,.2f}")

    bill = sum(s["usd_total"] for s in data)
    eps = episodes(info)
    ing_all = sum(r["res_bytes"] for r in info)
    cit_all = sum(r["cited_bytes"] for r in info)
    cc_all = ((ing_all - cit_all) / 1e6) / MB_PER_COMPACT * USD_PER_COMPACT
    print(f"\n-- compact cadence term: info loads put {ing_all/1e6:.2f} MB into the "
          f"window; cited-only would be {cit_all/1e6:.2f} MB")
    print(f"   at {MB_PER_COMPACT:.3f} MB/compact and ${USD_PER_COMPACT:.2f}/compact "
          f"=> {(ing_all-cit_all)/1e6/MB_PER_COMPACT:.1f} compacts avoided, worth ${cc_all:.2f}")

    print(f"\n-- episode-level: which delegations would ACTUALLY have paid "
          f"(one sub per episode, kept only where it wins; compact credit IN)")
    print(f"   {'model':18s} {'wins':>10s} {'saving':>8s} {'% info':>7s} {'% bill':>7s}")
    for m in ("sonnet", "haiku", "opus"):
        for prompt in ("stock", "stripped"):
            wins = []
            tot_today = tot_best = 0.0
            for e in eps:
                t, s_ = ep_cost(e, cpb, m, prompt)
                tot_today += t
                tot_best += min(t, s_)
                if s_ < t:
                    wins.append((t - s_, e, t, s_))
            wins.sort(key=lambda w: -w[0])
            save = tot_today - tot_best
            print(f"   {m+'/'+prompt:18s} {len(wins):5d}/{len(eps):<4d} ${save:7.2f} "
                  f"{100*save/max(ui,1e-9):6.1f}% {100*save/bill:6.2f}%")
            if m == "sonnet" and prompt == "stripped":
                for d, e, t, s_ in wins[:5]:
                    print(f"      +${d:5.2f}  {len(e):2d} loads "
                          f"{sum(r['res_bytes'] for r in e)/1024:6.0f} KB "
                          f"x{max(r['res_req'] for r in e):4d} req  {e[0]['shape'][:30]:30s} "
                          f"{(e[0].get('desc') or '')[:40]}")

    print(f"\n-- CEILING with both terms in (perfect oracle: carry only cited "
          f"bytes, sub runs free)")
    carry_now = sum(r["res_usd"] for r in info)
    oracle = sum(max(r["cited_bytes"], 200) * r["res_req"] * cpb for r in info)
    ceil = (carry_now - oracle) + cc_all
    print(f"   carriage ${carry_now:.2f} -> ${oracle:.2f} (${carry_now-oracle:.2f}) "
          f"+ compacts avoided ${cc_all:.2f} = ${ceil:.2f} = {100*ceil/bill:.1f}% of the bill")

    print(f"\n-- call-text (output-rate) side")
    print(f"   info-load call text: {sum(r['call_bytes'] for r in info):,} B emitted, "
          f"${sum(r['call_gen_usd'] for r in info):,.2f} generated + "
          f"${sum(r['call_carry_usd'] for r in info):,.2f} carried")
    print(f"   action call text:    {sum(r['call_bytes'] for r in act):,} B emitted, "
          f"${sum(r['call_gen_usd'] for r in act):,.2f} generated + "
          f"${sum(r['call_carry_usd'] for r in act):,.2f} carried")

    g = defaultdict(float)
    for s in data:
        for k, v in s["gen"].items():
            g[k] += v
    print(f"\n-- generation (off SSE): " +
          "  ".join(f"{k} ${v:,.2f}" for k, v in sorted(g.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
