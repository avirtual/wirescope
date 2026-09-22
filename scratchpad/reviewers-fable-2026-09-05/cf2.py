# Strip economics on reviewer seats, cross-priced.
# For each seat: actual token pattern (strip ON) and counterfactual (strip OFF: thinking retained,
# stock tail marker => new tail written once at 5m premium, read at cache_read afterwards).
# Price BOTH patterns under BOTH tables to split "price table" from "model behaviour".
import json,os,re,glob,time,sys,statistics as st
sys.path.insert(0,'/Users/bogdan/projects/proxy-lab')
from proxylab import billing
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
cut=time.time()-30*3600
RE=re.compile(r'clodex-clodex\.t(\d+)\.review-r(\d+)-')
P={m:billing._price_for(model='claude-'+m) for m in ('opus-5','fable-5-1')}
def W(t): return t.get('input_tokens',0)+t.get('cache_read_input_tokens',0)+t.get('cache_write_5m_tokens',0)+t.get('cache_write_1h_tokens',0)
seats=[]
for d in os.listdir(LD):
    p=os.path.join(LD,d)
    if d.startswith('_') or not os.path.isdir(p) or os.stat(p).st_mtime<cut: continue
    reqs=[]
    for f in sorted(glob.glob(p+'/*.response.json')):
        m=RE.search(f)
        if not m: continue
        o=json.load(open(f))
        if o.get('status_code')!=200: continue
        t=(o.get('billing') or {}).get('tokens') or {}
        if W(t)>2000: reqs.append((int(m.group(1)),int(m.group(2)),o.get('model').replace('claude-',''),t))
    if len(reqs)<3: continue
    # ON pattern: as billed. OFF pattern: rebuild.
    on={'in':0,'rd':0,'w':0,'out':0}; off={'in':0,'rd':0,'w':0,'out':0}
    prevW=0; thinks=[]; tails=[]
    for i,(_,_,_,t) in enumerate(reqs):
        on['in']+=t.get('input_tokens',0); on['rd']+=t.get('cache_read_input_tokens',0)
        on['w']+=t.get('cache_write_5m_tokens',0)+t.get('cache_write_1h_tokens',0); on['out']+=t.get('output_tokens',0)
        kept=sum(thinks[:-1]) if len(thinks)>1 else 0
        Wp=W(t)+kept
        if i==0: rd=t.get('cache_read_input_tokens',0); w=Wp-rd
        else: rd=prevW; w=max(0,Wp-prevW)
        off['rd']+=rd; off['w']+=w; off['out']+=t.get('output_tokens',0)
        if i>0: tails.append(W(t)-(prevW-kept))  # new bytes this round excl. retained thinking
        prevW=Wp; thinks.append(t.get('thinking_tokens',0))
    def price(pat,pr): return pat['in']*pr['in']/1e6+pat['rd']*pr['cache_read']/1e6+pat['w']*pr['cache_write_5m']/1e6+pat['out']*pr['out']/1e6
    seats.append(dict(t=reqs[0][0],r=reqs[0][1],model=reqs[0][2],n=len(reqs),think=sum(thinks),tail=sum(tails),
        cost={(pat,tab):price(on if pat=='on' else off,P[tab]) for pat in ('on','off') for tab in P}))
seats.sort(key=lambda s:(s['t'],s['r']))
print("seat            n  think/rnd tail/rnd T/S | opus$: on   off  Δ%  | fable$: on   off  Δ%")
for s in seats:
    c=s['cost']; rnd=max(1,s['n']-1)
    print(f"t{s['t']} r{s['r']} {s['model']:9} {s['n']:3d} {s['think']/rnd:8.0f} {s['tail']/rnd:8.0f} {s['think']/max(1,s['tail']):4.2f} | {c[('on','opus-5')]:5.2f} {c[('off','opus-5')]:5.2f} {100*(c[('on','opus-5')]/c[('off','opus-5')]-1):+4.0f}% | {c[('on','fable-5-1')]:5.2f} {c[('off','fable-5-1')]:5.2f} {100*(c[('on','fable-5-1')]/c[('off','fable-5-1')]-1):+4.0f}%")
print()
print("Population (seats' actual behaviour) x price table → strip ON cost relative to OFF (sum over seats):")
for m in ('opus-5','fable-5-1'):
    L=[s for s in seats if s['model']==m]
    row=[]
    for tab in ('opus-5','fable-5-1'):
        on=sum(s['cost'][('on',tab)] for s in L); off=sum(s['cost'][('off',tab)] for s in L)
        row.append(f"{tab} table: on ${on:.2f} off ${off:.2f} ({100*(on/off-1):+.0f}%)")
    print(f"  {m} behaviour ({len(L)} seats, T/S p50={st.median([s['think']/max(1,s['tail']) for s in L]):.2f}): "+' | '.join(row))
print()
# analytic break-even: strip pays per round iff T*(w + r*k) > S*in ; k = rounds remaining that would re-read the thinking
for m,pr in P.items():
    for k in (3,6,12,24):
        print(f"  {m}: break-even thinking/tail ratio at {k:2d} remaining rounds = {pr['in']/(pr['cache_write_5m']+pr['cache_read']*k):.2f}")
