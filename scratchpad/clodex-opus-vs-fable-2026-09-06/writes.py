import json,sys,re,statistics as st
from collections import Counter,defaultdict
sys.path.insert(0,'/Users/bogdan/projects/proxy-lab')
from proxylab import billing
coord=re.compile(r'^clodex-clodex-[0-9a-f]{8}$')
rs=[json.loads(l) for l in open('main_seat2.jsonl')]
rs=[r for r in rs if r.get('status')==200 and r.get('endpoint')=='messages' and 'usd' in r and coord.match(r.get('agent') or '') and r['role']=='parent' and r['ts']>='2026-08-20' and not r.get('ping') and not (r['out']<=1 and not r.get('text_chars') and not r.get('n_tool_uses'))]
rs.sort(key=lambda r:(r['ts_epoch'],r['seq']))
def W(r): return r['read']+r['inp']+r['w5']+r['w1']
coh={'opus-5 post-09-03':[r for r in rs if r['model']=='claude-opus-5' and r['ts']>='2026-09-03'],
     'fable-5-1':[r for r in rs if r['model']=='claude-fable-5-1']}
for name,x in coh.items():
    tot=sum(r['usd'] for r in x); n=len(x)
    p=billing._price_for(x[0]['model'])
    # classify writes
    cls=defaultdict(lambda:[0,0.0,0]) # tokens, usd, count
    prev=None
    for i,r in enumerate(x):
        w=r['w5']+r['w1']; usd=r['usd_w5']+r['usd_w1']
        if prev and prev.get('compact') and prev['session']==r['session']: k='post-compact re-cache'
        elif r.get('compact'): k='compact request itself'
        elif w>20000: k='bust (write>20k)'
        elif r['inp']>20000: k='uncached >20k (no marker)'
        else: k='normal tail write'
        cls[k][0]+=w; cls[k][1]+=usd; cls[k][2]+=1
        if k=='post-compact re-cache': cls[k][1]+=r['usd_in']  # count uncached-in too for this one
        prev=r
    print(f'\n== {name}: n={n} total ${tot:.2f}, write ${sum(r["usd_w5"]+r["usd_w1"] for r in x):.2f}')
    for k,(t,u,c) in sorted(cls.items(),key=lambda kv:-kv[1][1]):
        print(f'  {k:28} {c:5d} req  {t:>11,} tok  ${u:7.2f}  ({100*u/tot:4.1f}% of phase)  mean write/req {t/max(c,1):,.0f}')
    # per-request cost by stop reason / kind
    print('  $/req by kind: '+', '.join(f'{k} {v:.3f} (n={c})' for k,v,c in [(k,st.mean([r['usd'] for r in x if (r.get("stop") if not r.get("compact") else "compact")==k]),sum(1 for r in x if (r.get("stop") if not r.get("compact") else "compact")==k)) for k in ('tool_use','end_turn','compact')]))
    # first-request-of-turn vs continuation
    first=[]; cont=[]
    for i,r in enumerate(x):
        if i==0 or x[i-1].get('stop')!='tool_use' or x[i-1]['session']!=r['session']: first.append(r)
        else: cont.append(r)
    for lab,g in (('turn-opening req (carries the user/tool-result turn)',first),('continuation req (after tool_use)',cont)):
        print(f'  {lab:52} n={len(g):5d} $/req {st.mean([r["usd"] for r in g]):.4f}  write/req {st.mean([r["w5"]+r["w1"] for r in g]):,.0f}  out/req {st.mean([r["out"] for r in g]):,.0f}  uncached/req {st.mean([r["inp"] for r in g]):,.0f}')
    # gap distribution + TTL counterfactual
    gaps=[b['ts_epoch']-a['ts_epoch'] for a,b in zip(x,x[1:]) if a['session']==b['session']]
    g5=sum(1 for g in gaps if g>300); g60=sum(1 for g in gaps if g>3600)
    print(f'  inter-request gaps: >5min {g5} ({100*g5/len(gaps):.1f}%), >60min {g60}; p50 gap {st.median(gaps):.0f}s')
    w1=sum(r['w1'] for r in x); w5=sum(r['w5'] for r in x)
    save=w1*(p['cache_write_1h']-p['cache_write_5m'])/1e6
    # cost of going cold at 5m: each >5min gap (that is currently warm at 1h, i.e. <60min) re-writes the window at 5m rate
    cold_cost=0; 
    for a,b in zip(x,x[1:]):
        if a['session']==b['session'] and 300<(b['ts_epoch']-a['ts_epoch'])<=3600:
            cold_cost+=b['read']*(p['cache_write_5m']-p['cache_read'])/1e6
    print(f'  TTL counterfactual: 1h->5m premium saved ${save:.2f}, extra cold re-writes ${cold_cost:.2f} -> net {"+" if save-cold_cost>0 else ""}{save-cold_cost:.2f} (positive = 5m cheaper)')
    # top-10 most expensive requests
    top=sorted(x,key=lambda r:-r['usd'])[:6]
    print('  top $ requests: '+'; '.join(f"${r['usd']:.2f} w={r['w5']+r['w1']:,} in={r['inp']:,} out={r['out']:,}{' COMPACT' if r.get('compact') else ''}{' bust:'+str(r.get('bust_class')) if r.get('bust_class') else ''}" for r in top))
