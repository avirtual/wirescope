import json,os,re,glob,statistics as st,time,datetime as dt
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
cut=time.time()-30*3600
RE=re.compile(r'clodex-clodex\.t(\d+)\.review-r(\d+)-')
VR=re.compile(r'\*\*VERDICT\*\*:?\s*\**\s*(\w+)')
seats={}
for d in os.listdir(LD):
    p=os.path.join(LD,d)
    if d.startswith('_') or not os.path.isdir(p) or os.stat(p).st_mtime<cut: continue
    for f in sorted(glob.glob(p+'/*.response.json')):
        m=RE.search(f)
        if not m: continue
        o=json.load(open(f)); b=o.get('billing') or {}; t=b.get('tokens') or {}; me=o.get('meta') or {}
        key=(int(m.group(1)),int(m.group(2)),d[:8])
        s=seats.setdefault(key,{'model':None,'reqs':[],'files':set(),'greps':0,'verdict':None,'t0':None,'read_tok':0,'write_tok':0,'out_tok':0,'usd_read':0,'usd_write':0,'usd_out':0,'usd_in':0})
        s['model']=o.get('model') or s['model']
        w=(t.get('input_tokens') or 0)+(t.get('cache_read_input_tokens') or 0)+(t.get('cache_write_5m_tokens') or 0)+(t.get('cache_write_1h_tokens') or 0)
        tus=me.get('tool_uses') or []
        s['reqs'].append({'w':w,'out':t.get('output_tokens') or 0,'think':t.get('thinking_tokens') or 0,'ntools':len(tus),'stop':me.get('stop_reason'),'usd':b.get('est_usd') or 0,'ts':os.stat(f).st_mtime})
        s['t0']=min(s['t0'] or 1e12,os.stat(f).st_mtime)
        from proxylab import billing
        try:
            pr=billing._price_for(model=o.get('model'))
            s['usd_read']+=(t.get('cache_read_input_tokens') or 0)*pr['cache_read']/1e6
            s['usd_write']+=(t.get('cache_write_5m_tokens') or 0)*pr['cache_write_5m']/1e6+(t.get('cache_write_1h_tokens') or 0)*pr['cache_write_1h']/1e6
            s['usd_out']+=(t.get('output_tokens') or 0)*pr['out']/1e6
            s['usd_in']+=(t.get('input_tokens') or 0)*pr['in']/1e6
        except Exception as e: pass
        for tu in tus:
            if not isinstance(tu,dict): continue
            inp=tu.get('input') or {}
            if tu.get('name')=='Read': s['files'].add(inp.get('file_path'))
            if tu.get('name') in('Grep','Glob'): s['greps']+=1
        if me.get('stop_reason')=='end_turn':
            v=VR.search(me.get('text') or '')
            if v: s['verdict']=v.group(1).upper()
import sys; sys.path.insert(0,'/Users/bogdan/projects/proxy-lab')
rows=[(k,s) for k,s in sorted(seats.items()) if len(s['reqs'])>=3]
by={}
print("ticket r  model      start(local)  req  tools/req  out/req  distinct_files  greps  win2nd  winlast  usd  read  write  out   verdict")
for (t,r,sid),s in rows:
    m=s['model'].replace('claude-',''); by.setdefault(m,[]).append(s)
    R=s['reqs']; real=[x for x in R if x['w']>2000]
    nt=sum(x['ntools'] for x in R)
    s['tools_per_req']=nt/max(1,len(real)); s['out_per_req']=sum(x['out'] for x in R)/max(1,len(real))
    s['win2']=real[0]['w'] if real else 0; s['winlast']=real[-1]['w'] if real else 0
    s['nreal']=len(real); s['usd']=sum(x['usd'] for x in R)
    print(f"t{t:<4} r{r} {m:9} {dt.datetime.fromtimestamp(s['t0']).strftime('%m-%d %H:%M')}  {len(real):3d}  {s['tools_per_req']:6.2f}   {s['out_per_req']:6.0f}   {len(s['files']):6d}     {s['greps']:4d}  {s['win2']:6d}  {s['winlast']:7d}  {s['usd']:5.2f} {s['usd_read']:5.2f} {s['usd_write']:5.2f} {s['usd_out']:5.2f}  {s['verdict']}")
print()
for m,L in by.items():
    med=lambda f: st.median([f(x) for x in L])
    tot=sum(x['usd'] for x in L)
    print(f"{m}: seats={len(L)} real_req p50={med(lambda x:x['nreal'])} | tools/req p50={med(lambda x:x['tools_per_req']):.2f} | out/req p50={med(lambda x:x['out_per_req']):.0f} | distinct files p50={med(lambda x:len(x['files']))} | greps p50={med(lambda x:x['greps'])} | win2nd p50={med(lambda x:x['win2'])} | winlast p50={med(lambda x:x['winlast'])} | usd p50={med(lambda x:x['usd']):.2f} | cost split read {sum(x['usd_read'] for x in L)/tot:.0%} write {sum(x['usd_write'] for x in L)/tot:.0%} out {sum(x['usd_out'] for x in L)/tot:.0%} in {sum(x['usd_in'] for x in L)/tot:.0%} | verdicts={ {v:sum(1 for x in L if x['verdict']==v) for v in set(x['verdict'] for x in L)} }")
# r1 only, since rounds differ
print()
for m,L in by.items():
    L1=[s for (k,s) in rows if s['model'].replace('claude-','')==m and k[1]==1]
    if L1: print(f"{m} r1-only: seats={len(L1)} req p50={st.median([x['nreal'] for x in L1])} usd p50={st.median([x['usd'] for x in L1]):.2f} files p50={st.median([len(x['files']) for x in L1])} winlast p50={st.median([x['winlast'] for x in L1])}")
