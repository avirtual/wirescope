import json,os,re,sys,glob,statistics as st,time
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
HOURS=float(sys.argv[1]) if len(sys.argv)>1 else 48
cut=time.time()-HOURS*3600
RE=re.compile(r'clodex-clodex\.t(\d+)\.review-r(\d+)-')
seats={}
for d in os.listdir(LD):
    p=os.path.join(LD,d)
    if not os.path.isdir(p) or os.stat(p).st_mtime<cut: continue
    for f in sorted(glob.glob(p+'/*.response.json')):
        m=RE.search(f)
        if not m: continue
        try: o=json.load(open(f))
        except Exception: continue
        key=(int(m.group(1)),int(m.group(2)),d[:8])
        s=seats.setdefault(key,{'model':o.get('model'),'n':0,'usd':0,'out':0,'think':0,'win':[],'stops':{},'status':{},'first':None,'last':None,'tools':0,'refusal':0,'errs':0})
        b=o.get('billing') or {}; t=b.get('tokens') or {}
        me=o.get('meta') or {}
        s['n']+=1; s['usd']+=b.get('est_usd') or 0
        s['out']+=t.get('output_tokens') or 0; s['think']+=t.get('thinking_tokens') or 0
        w=(t.get('input_tokens') or 0)+(t.get('cache_read_input_tokens') or 0)+(t.get('cache_write_5m_tokens') or 0)+(t.get('cache_write_1h_tokens') or 0)
        s['win'].append(w)
        sr=me.get('stop_reason'); s['stops'][sr]=s['stops'].get(sr,0)+1
        sc=o.get('status_code'); s['status'][sc]=s['status'].get(sc,0)+1
        if sc and sc!=200: s['errs']+=1
        if sr=='refusal': s['refusal']+=1
        tu=me.get('tool_uses') or []
        s['tools']+=len(tu) if isinstance(tu,list) else 0
        mt=os.stat(f).st_mtime
        s['first']=mt if s['first'] is None else min(s['first'],mt); s['last']=mt if s['last'] is None else max(s['last'],mt)
        s['model']=o.get('model') or s['model']
rows=sorted(seats.items())
print(f"{'ticket':>6} {'r':>2} sess     model            n   usd    out  think  win_last  win_max  dur_min tools  errs stops")
by={}
for (t,r,sid),s in rows:
    m=(s['model'] or '?').replace('claude-','')
    by.setdefault(m,[]).append(s)
    print(f"t{t:<5} r{r:<2} {sid} {m:15} {s['n']:3d} {s['usd']:6.2f} {s['out']:6d} {s['think']:6d} {s['win'][-1]:9d} {max(s['win']):8d} {(s['last']-s['first'])/60:7.1f} {s['tools']:5d} {s['errs']:4d} {dict(s['stops'])}")
print()
for m,L in by.items():
    def med(k): return st.median([x[k] for x in L])
    def mean(k): return st.mean([x[k] for x in L])
    print(f"{m}: seats={len(L)} n p50={med('n')} mean={mean('n'):.1f} | usd p50={med('usd'):.2f} mean={mean('usd'):.2f} total={sum(x['usd'] for x in L):.2f} | out p50={med('out')} | think p50={med('think')} | win_last p50={st.median([x['win'][-1] for x in L])} | dur p50={st.median([(x['last']-x['first'])/60 for x in L]):.1f}m | tools/seat p50={med('tools')} | usd/req mean={sum(x['usd'] for x in L)/sum(x['n'] for x in L):.3f}")
