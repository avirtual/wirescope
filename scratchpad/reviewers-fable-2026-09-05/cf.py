# Counterfactual: same reviewer session with mid-turn thinking strip OFF
# (thinking kept, CLI tail marker untouched): each request writes its new tail at
# the 5m premium and reads everything else at cache-read; window grows by the
# thinking we would no longer strip. Compare to the actual receipt USD.
import json,os,re,glob,time,sys,statistics as st
sys.path.insert(0,'/Users/bogdan/projects/proxy-lab')
from proxylab import billing
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
cut=time.time()-30*3600
RE=re.compile(r'clodex-clodex\.t(\d+)\.review-r(\d+)-')
rows=[]
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
        reqs.append((int(m.group(1)),int(m.group(2)),o.get('model'),t,(o.get('billing') or {}).get('est_usd') or 0))
    real=[r for r in reqs if (r[3].get('input_tokens',0)+r[3].get('cache_read_input_tokens',0)+r[3].get('cache_write_5m_tokens',0)+r[3].get('cache_write_1h_tokens',0))>2000]
    if len(real)<3: continue
    t_,r_,model=real[0][0],real[0][1],real[0][2]
    pr=billing._price_for(model=model)
    actual=sum(x[4] for x in reqs)
    # decompose actual
    a_in=a_rd=a_w=a_out=0
    for _,_,_,t,_ in reqs:
        a_in+=t.get('input_tokens',0); a_rd+=t.get('cache_read_input_tokens',0)
        a_w+=t.get('cache_write_5m_tokens',0)+t.get('cache_write_1h_tokens',0); a_out+=t.get('output_tokens',0)
    # counterfactual over the real (tool-carrying) requests; non-real ones (title/probe) cost the same in both
    cf=sum(x[4] for x in reqs if x not in real)
    prevW=0; kept=0; thinks=[]
    cf_in=cf_rd=cf_w=0
    for i,(_,_,_,t,usd) in enumerate(real):
        W=t.get('input_tokens',0)+t.get('cache_read_input_tokens',0)+t.get('cache_write_5m_tokens',0)+t.get('cache_write_1h_tokens',0)
        kept=sum(thinks[:-1]) if len(thinks)>1 else 0   # thinking stripped so far (all but the live one)
        Wp=W+kept
        if i==0:
            w=Wp-t.get('cache_read_input_tokens',0); rd=t.get('cache_read_input_tokens',0)
        else:
            rd=prevW; w=max(0,Wp-prevW)
        cf+= rd*pr['cache_read']/1e6 + w*pr['cache_write_5m']/1e6 + t.get('output_tokens',0)*pr['out']/1e6
        cf_rd+=rd; cf_w+=w
        prevW=Wp
        thinks.append(t.get('thinking_tokens',0))
    rows.append(dict(t=t_,r=r_,model=model.replace('claude-',''),n=len(real),actual=actual,cf=cf,a_in=a_in,a_rd=a_rd,a_w=a_w,a_out=a_out,cf_rd=cf_rd,cf_w=cf_w,think=sum(thinks)))
rows.sort(key=lambda x:(x['t'],x['r']))
print("ticket r  model      n  actual   cf   delta   %    | actual tok: in1x   rd     w     out  think | cf: rd      w")
for x in rows:
    print(f"t{x['t']} r{x['r']} {x['model']:9} {x['n']:3d} {x['actual']:6.2f} {x['cf']:6.2f} {x['actual']-x['cf']:6.2f} {100*(x['actual']-x['cf'])/x['actual']:4.0f}%  | {x['a_in']:7d} {x['a_rd']:7d} {x['a_w']:6d} {x['a_out']:6d} {x['think']:6d} | {x['cf_rd']:7d} {x['cf_w']:6d}")
print()
for m in ('opus-5','fable-5-1'):
    L=[x for x in rows if x['model']==m]
    A=sum(x['actual'] for x in L); C=sum(x['cf'] for x in L)
    print(f"{m}: seats={len(L)} actual=${A:.2f} counterfactual(no mid-turn strip)=${C:.2f} delta=${A-C:.2f} ({100*(A-C)/A:.0f}%) | per-seat delta p50={st.median([x['actual']-x['cf'] for x in L]):.2f} | uncached-1x tokens/seat p50={st.median([x['a_in'] for x in L]):.0f} = {100*st.median([x['a_in']/(x['a_in']+x['a_rd']+x['a_w']) for x in L]):.0f}% of window-tokens | thinking/seat p50={st.median([x['think'] for x in L]):.0f}")
