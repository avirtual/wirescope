#!/usr/bin/env python3
"""Report from inbound.py output: what enters the window per request, by source,
priced at write rate + re-reads until the next compaction.

usage: inbound_report.py <label> <model> <recs.json>
"""
import json, sys, statistics as st
from collections import Counter, defaultdict
sys.path.insert(0, '/Users/bogdan/projects/proxy-lab')
from proxylab import billing

CH = 3.0
label, model, path = sys.argv[1:4]
p = billing._price_for(model)
recs = json.load(open(path))
delta = [r for r in recs if r['mode'] == 'delta']
reb = [r for r in recs if r['mode'] != 'delta']
n = len(recs)
print(f'\n===== {label}: {n} requests ({len(delta)} delta, {len(reb)} first/rebuild/realign) =====')
tot_w = sum(r['w'] for r in recs); tot_in = sum(r['inp'] for r in recs); tot_out = sum(r['out'] for r in recs); tot_read = sum(r['read'] for r in recs)
usd_w = tot_w * p['cache_write_1h'] / 1e6; usd_in = tot_in * p['in'] / 1e6; usd_out = tot_out * p['out'] / 1e6; usd_rd = tot_read * p['cache_read'] / 1e6
tot = usd_w + usd_in + usd_out + usd_rd
print(f'receipts: write {tot_w:,} tok ${usd_w:.2f} | uncached {tot_in:,} ${usd_in:.2f} | output {tot_out:,} ${usd_out:.2f} | read {tot_read:,} ${usd_rd:.2f} | total ${tot:.2f}')
print(f'per request: window growth (write+uncached) {(tot_w+tot_in)/n:,.0f} tok, output {tot_out/n:,.0f} tok')

# category totals (delta requests only = organic growth) in chars -> est tokens
cat = Counter(); cnt = Counter()
for r in delta:
    for k, v in r['cat'].items():
        cat[k] += v; cnt[k] += 1
est_total = sum(cat.values()) / CH
measured = sum(r['w'] + r['inp'] for r in delta)
print(f'\norganic growth on delta requests: classified {est_total:,.0f} est tok vs receipts write+uncached {measured:,} tok (ratio {est_total/max(measured,1):.2f}; JSON framing + tool_use ids are the gap)')
print(f'\n{"source":28} {"est tok":>10} {"share":>6} {"req w/":>6} {"tok/req":>8} {"$ write":>8} {"$ re-read*":>10} {"$ total":>8}')
# re-read multiplier: every appended token is re-read on each later request in its epoch. mean remaining requests ~ half the epoch length
epoch = [len(x) for x in [[]]]  # placeholder
# compute mean remaining requests until rebuild
rem = []
since = 0
for r in reversed(recs):
    if r['mode'] != 'delta': since = 0
    else: rem.append(since); since += 1
mean_rem = st.mean(rem) if rem else 50
rows = []
for k, v in cat.most_common():
    tok = v / CH
    w = tok * p['cache_write_1h'] / 1e6
    rr = tok * mean_rem * p['cache_read'] / 1e6
    rows.append((k, tok, w, rr, cnt[k]))
for k, tok, w, rr, c in rows:
    print(f'{k:28} {tok:10,.0f} {100*tok/est_total:5.1f}% {c:6d} {tok/max(c,1):8,.0f} {w:8.2f} {rr:10.2f} {w+rr:8.2f}')
print(f'* re-read = tok x mean remaining requests in epoch ({mean_rem:.0f}) x read rate; write at 1h rate')

# group view
groups = {
 'seat output (text+tool_input+thinking)': ['out_text', 'out_tool_input', 'out_thinking'],
 'tool results (Bash/Read/etc, not dm reads)': [k for k in cat if k.startswith('tool:') and k not in ('tool:read_dm_attachment', 'tool:read_task_file')],
 'task files read (JOURNAL/live.md/tasks)': ['tool:read_task_file'],
 'peer dms incl. attached bodies': ['dm_hand', 'dm_reviewer', 'dm_peer', 'dm_hand_body', 'dm_reviewer_body', 'dm_peer_body', 'tool:read_dm_attachment'],
 'task board / ticket-loop': ['task_board'],
 'reminders': ['reminder', 'reminder_body'],
 'terminal/exec': ['terminal'],
 'operator text': ['operator', 'boot_prompt'],
 'CLI injections (sys reminders, rosters, commands, memory)': ['cli_system_reminder', 'cli_env_reminder', 'cli_command', 'cli_memory_attach', 'cli_roster_system', 'cli_autoread'],
 'intent acks': ['intent_ack'],
 'compact instruction + summary': ['compact_instruction', 'compact_summary'],
}
print(f'\n{"group":58} {"est tok":>10} {"share":>6} {"$ write+re-read":>15}')
seen = set()
for g, ks in groups.items():
    tok = sum(cat[k] for k in ks) / CH; seen |= set(ks)
    print(f'{g:58} {tok:10,.0f} {100*tok/est_total:5.1f}% {tok*(p["cache_write_1h"]+mean_rem*p["cache_read"])/1e6:15.2f}')
rest = sum(v for k, v in cat.items() if k not in seen) / CH
print(f'{"other: "+", ".join(k for k in cat if k not in seen):58} {rest:10,.0f} {100*rest/est_total:5.1f}%')

# rebuild cost
if reb:
    rc = Counter()
    for r in reb:
        for k, v in r['cat'].items(): rc[k.replace('REBUILD:', '')] += v
    rt = sum(rc.values()) / CH
    print(f'\nrebuilds (post-compact / restart) x{len(reb)}: {rt:,.0f} est tok total, {rt/len(reb):,.0f} per rebuild; receipts write+uncached on those requests {sum(r["w"]+r["inp"] for r in reb):,} tok = ${sum(r["w"]+r["inp"] for r in reb)*p["cache_write_1h"]/1e6:.2f}')
    for k, v in rc.most_common(8): print(f'   {k:28} {v/CH:10,.0f}')

# biggest single items
items = sorted((it for r in delta for it in r['items']), reverse=True)[:15]
print('\nlargest single blocks appended (delta requests):')
for ln, c, note in items: print(f'  {ln/CH:8,.0f} tok  {c:24} {note[:100]}')

# per-request distribution of growth
g = [sum(r['cat'].values()) / CH for r in delta]
print(f'\ngrowth per delta request: p50 {st.median(g):,.0f}  p90 {sorted(g)[int(.9*len(g))]:,.0f}  mean {st.mean(g):,.0f} est tok;  new user msgs/req mean {st.mean([r["new_user_msgs"] for r in delta]):.2f}')
