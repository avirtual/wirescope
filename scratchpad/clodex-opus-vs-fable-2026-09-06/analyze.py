#!/usr/bin/env python3
"""Opus-5 vs Fable-5-1 on the clodex coordinator seat, from extract.py's JSONL.

usage: analyze.py main_seat.jsonl [--since YYYY-MM-DD] [--until YYYY-MM-DD]
"""
import json, sys, re, statistics as st
from collections import Counter, defaultdict
sys.path.insert(0, '/Users/bogdan/projects/proxy-lab')
from proxylab import billing

OPUS, FABLE = 'claude-opus-5', 'claude-fable-5-1'
PINGS = []

def q(xs, p):
    xs = sorted(xs)
    if not xs: return 0
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]

def fmt_usd(x): return f'${x:,.2f}'

def window(r): return r['read'] + r['inp'] + r['w5'] + r['w1']

def price_as(r, model):
    """Cross-price a receipt's tokens at another model's rate table."""
    p = billing._price_for(model)
    u = lambda n, k: n * p[k] / 1e6
    return u(r['inp'], 'in') + u(r['out'], 'out') + u(r['read'], 'cache_read') + u(r['w5'], 'cache_write_5m') + u(r['w1'], 'cache_write_1h')

def load(path, since=None, until=None):
    rs = []
    for l in open(path):
        r = json.loads(l)
        if r.get('status') != 200 or r.get('endpoint') != 'messages': continue
        if r.get('unpriced') or 'usd' not in r: continue
        if r.get('ping') or (r.get('out', 0) <= 1 and not r.get('text_chars') and not r.get('n_tool_uses')):
            PINGS.append(r); continue
        if since and (r.get('ts') or '')[:10] < since: continue
        if until and (r.get('ts') or '')[:10] > until: continue
        rs.append(r)
    rs.sort(key=lambda r: (r['ts_epoch'] or 0, r['seq']))
    return rs

def turns_of(reqs):
    """Group consecutive main-line requests into turns: a turn ends at a non-tool_use stop."""
    turns, cur = [], []
    for r in reqs:
        cur.append(r)
        if r.get('stop') != 'tool_use':
            turns.append(cur); cur = []
    if cur: turns.append(cur)
    return turns

def phase_block(name, reqs):
    n = len(reqs)
    if not n: return
    usd = [r['usd'] for r in reqs]
    turns = turns_of(reqs)
    tusd = [sum(x['usd'] for x in t) for t in turns]
    treq = [len(t) for t in turns]
    win = [window(r) for r in reqs]
    tot = sum(usd)
    comp = dict(read=sum(r['usd_read'] for r in reqs), write=sum(r['usd_w5'] + r['usd_w1'] for r in reqs),
                inp=sum(r['usd_in'] for r in reqs), out=sum(r['usd_out'] for r in reqs))
    hours = (reqs[-1]['ts_epoch'] - reqs[0]['ts_epoch']) / 3600 if n > 1 else 0
    # active hours: sum of gaps < 30 min
    act = sum(min(1800, b['ts_epoch'] - a['ts_epoch']) for a, b in zip(reqs, reqs[1:])) / 3600
    print(f'\n== {name}  n={n} requests, {len(turns)} turns, {reqs[0]["ts"][:16]} .. {reqs[-1]["ts"][:16]} '
          f'(span {hours:.0f}h, active {act:.1f}h) ==')
    print(f'  total {fmt_usd(tot)}  = read {fmt_usd(comp["read"])} ({100*comp["read"]/tot:.0f}%)  write {fmt_usd(comp["write"])} ({100*comp["write"]/tot:.0f}%)  '
          f'uncached-in {fmt_usd(comp["inp"])} ({100*comp["inp"]/tot:.0f}%)  output {fmt_usd(comp["out"])} ({100*comp["out"]/tot:.0f}%)')
    print(f'  $/request  mean {tot/n:.4f}  p50 {q(usd,.5):.4f}  p90 {q(usd,.9):.4f}   |  $/active-hour {tot/max(act,1e-9):.2f}  req/active-hour {n/max(act,1e-9):.0f}')
    print(f'  $/turn     mean {tot/len(turns):.3f}  p50 {q(tusd,.5):.3f}  p90 {q(tusd,.9):.3f}   |  req/turn mean {n/len(turns):.2f} p50 {q(treq,.5)} p90 {q(treq,.9)}')
    print(f'  window/req mean {st.mean(win):,.0f}  p50 {q(win,.5):,.0f}  p90 {q(win,.9):,.0f}   '
          f'|  per req: read {st.mean([r["read"] for r in reqs]):,.0f}  write {st.mean([r["w5"]+r["w1"] for r in reqs]):,.0f}  '
          f'uncached {st.mean([r["inp"] for r in reqs]):,.0f}  out {st.mean([r["out"] for r in reqs]):,.0f}  think {st.mean([r["think"] for r in reqs]):,.0f}')
    tu = Counter(); ntu = 0
    for r in reqs:
        tu.update(r.get('tool_uses') or []); ntu += r.get('n_tool_uses') or 0
    text_only = sum(1 for r in reqs if not r.get('n_tool_uses'))
    print(f'  tool calls/req {ntu/n:.2f}  text-only responses {100*text_only/n:.0f}%  '
          f'top tools: ' + ', '.join(f'{k} {v}' for k, v in tu.most_common(8)))
    ic = Counter()
    for r in reqs: ic.update(r.get('intents') or {})
    ni = sum(ic.values())
    tchars = sum(r.get('text_chars') or 0 for r in reqs)
    print(f'  coordinator intents: {ni} total, {ni/len(turns):.2f}/turn, $/intent {tot/max(ni,1):.3f}  -> ' + ', '.join(f'{k} {v}' for k, v in ic.most_common(9)))
    print(f'  visible text chars/turn {tchars/len(turns):,.0f}  output tok/turn {sum(r["out"] for r in reqs)/len(turns):,.0f}  thinking tok/turn {sum(r["think"] for r in reqs)/len(turns):,.0f}')
    cold = sum(1 for r in reqs if r.get('warm_on_arrival') is False)
    busts = sum(1 for r in reqs if r.get('bust_class'))
    print(f'  warm-on-arrival false: {cold} ({100*cold/n:.1f}%)   bust_class set: {busts}   ttl: {Counter(r.get("ttl") for r in reqs).most_common(2)}')
    # cross-pricing: what would this SAME traffic cost at the other model's rates
    other = FABLE if reqs[0]['model'] == OPUS else OPUS
    xp = sum(price_as(r, other) for r in reqs)
    print(f'  cross-priced at {other} rates: {fmt_usd(xp)} ({xp/tot:.2f}x)')
    return dict(n=n, turns=len(turns), tot=tot, act=act)

def compact_block(name, reqs, all_reqs_in_phase):
    comps = [r for r in reqs if r.get('compact')]
    print(f'\n-- compactions in {name}: {len(comps)}')
    if not comps: return
    usd = [r['usd'] for r in comps]
    ctx = [window(r) for r in comps]
    out = [r['out'] for r in comps]
    cold = [r for r in comps if r['inp'] > 0.5 * window(r)]
    print(f'  $/compact mean {st.mean(usd):.3f} p50 {q(usd,.5):.3f} max {max(usd):.3f}  total {fmt_usd(sum(usd))}  '
          f'= {100*sum(usd)/sum(r["usd"] for r in reqs):.1f}% of phase spend')
    print(f'  context in: p50 {q(ctx,.5):,}  max {max(ctx):,}   summary out: p50 {q(out,.5):,}  p90 {q(out,.9):,}  '
          f'ratio p50 {q([o/c for o,c in zip(out,ctx)],.5):.3f}')
    print(f'  cold (uncached input > 50% of window): {len(cold)}/{len(comps)}   '
          f'output share of compact $: {100*sum(r["usd_out"] for r in comps)/sum(usd):.0f}%')
    # epochs between compactions (main-line requests only, within this phase's requests)
    epochs = []; shares = []
    by_sess = defaultdict(list)
    for r in reqs: by_sess[r['session']].append(r)
    for sreqs in by_sess.values():
        idx = [i for i, r in enumerate(sreqs) if r.get('compact')]
        for a, b in zip(idx, idx[1:]):
            seg = sreqs[a + 1:b]          # between two compacts, excl. the compact requests
            if not seg: continue
            turns = turns_of(seg)
            epochs.append(dict(req=len(seg), turns=len(turns), usd=sum(r['usd'] for r in seg),
                               hours=(seg[-1]['ts_epoch'] - seg[0]['ts_epoch']) / 3600,
                               first_win=window(seg[0]), last_win=window(seg[-1]),
                               model=seg[0]['model'], intents=sum(sum((r.get('intents') or {}).values()) for r in seg)))
            shares.append(sreqs[b]['usd'] / (sum(r['usd'] for r in seg) + sreqs[b]['usd']))
    if epochs:
        print(f'  full epochs between compacts: {len(epochs)}')
        for k, lab in (('req', 'requests'), ('turns', 'turns'), ('usd', '$'), ('hours', 'wall hours'), ('intents', 'intents')):
            xs = [e[k] for e in epochs]
            print(f'    {lab:10} per epoch: mean {st.mean(xs):8.2f}  p50 {q(xs,.5):8.2f}  p90 {q(xs,.9):8.2f}')
        print(f'    window after compact: p50 {q([e["first_win"] for e in epochs],.5):,}   window at next compact: p50 {q([e["last_win"] for e in epochs],.5):,}')
        print(f'    compact $ / (epoch $ + compact $): mean {100*st.mean(shares):.1f}%  p50 {100*q(shares,.5):.1f}%')

def daily(reqs):
    print('\n== per day (main line) ==')
    print(f'{"day":10} {"model":16} {"req":>5} {"turns":>5} {"$":>8} {"$/req":>7} {"$/turn":>7} {"win p50":>8} {"comp":>4} {"comp$":>6} {"active h":>8}')
    by = defaultdict(list)
    for r in reqs: by[(r['ts'][:10], r['model'])].append(r)
    for (d, m), rs in sorted(by.items()):
        turns = turns_of(rs); tot = sum(r['usd'] for r in rs)
        comps = [r for r in rs if r.get('compact')]
        act = sum(min(1800, b['ts_epoch'] - a['ts_epoch']) for a, b in zip(rs, rs[1:])) / 3600
        print(f'{d:10} {m[7:]:16} {len(rs):5d} {len(turns):5d} {tot:8.2f} {tot/len(rs):7.4f} {tot/max(1,len(turns)):7.3f} {q([window(r) for r in rs],.5):8,} {len(comps):4d} {sum(r["usd"] for r in comps):6.2f} {act:8.1f}')

def main():
    path = sys.argv[1]; since = until = None
    a = sys.argv[2:]
    while a:
        k = a.pop(0)
        if k == '--since': since = a.pop(0)
        elif k == '--until': until = a.pop(0)
    rs = load(path, since, until)
    coord = re.compile(r'^clodex-clodex-[0-9a-f]{8}$')
    rs = [r for r in rs if coord.match(r.get('agent') or '')]
    main_line = [r for r in rs if r.get('role') == 'parent']
    subs = [r for r in rs if r.get('role') != 'parent']
    print(f'loaded {len(rs)} priced 200s: main-line {len(main_line)}, sub/side-calls {len(subs)}; sessions {len({r["session"] for r in rs})}')
    print('models on main line:', Counter(r['model'] for r in main_line).most_common())
    print('sessions:')
    for s, n in Counter(r['session'] for r in main_line).most_common():
        ss = [r for r in main_line if r['session'] == s]
        print(f'  {s[:8]}  {ss[0]["ts"][:16]} .. {ss[-1]["ts"][:16]}  n={n}  {Counter(r["model"] for r in ss).most_common()}  ${sum(r["usd"] for r in ss):.2f}')
    cohorts = [
        (OPUS + ' ALL', [r for r in main_line if r['model'] == OPUS]),
        (OPUS + ' post-2026-09-03 (v0.6.58 live: compact strip retired, same regime as fable)', [r for r in main_line if r['model'] == OPUS and r['ts'] >= '2026-09-03']),
        (OPUS + ' same seat 5383fbbc before the switch', [r for r in main_line if r['model'] == OPUS and r['session'].startswith('5383fbbc')]),
        (FABLE, [r for r in main_line if r['model'] == FABLE]),
    ]
    for name, ph in cohorts:
        phase_block(name + ' (main line)', ph)
        compact_block(name, ph, ph)
    if PINGS:
        pc = Counter((r['model'], r['session'][:8]) for r in PINGS)
        print(f'\n== hold/keep-warm pings (excluded above): {len(PINGS)} req, ${sum(r["usd"] for r in PINGS):.2f}  ' + ', '.join(f'{m[7:]}/{s} {n}' for (m, s), n in pc.most_common(6)))
    # the other models (opus-4.8 etc) just for completeness
    others = [r for r in main_line if r['model'] not in (OPUS, FABLE)]
    if others:
        print(f'\n(other main-line models: {Counter(r["model"] for r in others).most_common()}  ${sum(r["usd"] for r in others):.2f})')
    # sub-agent / side-call line, by phase of the parent at that time (approx: by model of the sub)
    if subs:
        print('\n== subagent + side-call line (spawned by the seat, shares session_id) ==')
        by = defaultdict(list)
        for r in subs: by[(r['model'], r['role'])].append(r)
        for (m, role), x in sorted(by.items(), key=lambda kv: -sum(r['usd'] for r in kv[1])):
            print(f'  {m:28} {role:16} n={len(x):5d}  ${sum(r["usd"] for r in x):8.2f}  $/req {sum(r["usd"] for r in x)/len(x):.4f}')
        # attribute subs to the parent's phase by timestamp: parent model at nearest earlier main-line request
        import bisect
        keys = [r['ts_epoch'] for r in main_line]
        att = defaultdict(float); attn = Counter()
        for r in subs:
            i = bisect.bisect_right(keys, r['ts_epoch']) - 1
            if i >= 0:
                att[main_line[i]['model']] += r['usd']; attn[main_line[i]['model']] += 1
        for m in (OPUS, FABLE):
            ph = [r for r in main_line if r['model'] == m]
            if ph:
                mt = sum(r['usd'] for r in ph)
                print(f'  attributed to {m} phase: {attn[m]} sub-requests, ${att[m]:.2f} (= {100*att[m]/mt:.1f}% on top of the main line ${mt:.2f})')
    daily(main_line)

if __name__ == '__main__':
    main()
