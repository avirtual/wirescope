import json,os,re,glob,time,statistics as st,datetime as dt,collections
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
HOURS=float(os.environ.get('H','120'))
cut=time.time()-HOURS*3600
RE=re.compile(r'clodex-clodex\.t(\d+)\.hand-')
def W(t): return sum((t.get(k) or 0) for k in ('input_tokens','cache_read_input_tokens','cache_write_5m_tokens','cache_write_1h_tokens'))
seats={}
for d in os.listdir(LD):
    p=os.path.join(LD,d)
    if d.startswith('_') or not os.path.isdir(p) or os.stat(p).st_mtime<cut: continue
    fs=[f for f in sorted(glob.glob(p+'/*.response.json'),key=lambda f:int(os.path.basename(f).split('-')[0])) if RE.search(f) and '-parent-' in f]
    if not fs: continue
    t_=int(RE.search(fs[0]).group(1))
    s=seats.setdefault((t_,d[:8]),dict(model=collections.Counter(),n=0,usd=0,out=0,think=0,first=None,last=None,tools=collections.Counter(),win=0,turns=0,edits=set(),bash_edits=0,tests=0,fails=0,errs=0,compacts=0,done=0,maxtok=0,compaction=0))
    for f in fs:
        try: o=json.load(open(f))
        except: continue
        if o.get('status_code')!=200: continue
        t={k:(v or 0) for k,v in ((o.get('billing') or {}).get('tokens') or {}).items()}
        me=o.get('meta') or {}
        if W(t)<2000: continue
        s['model'][o.get('model')]+=1; s['n']+=1; s['usd']+=(o.get('billing') or {}).get('est_usd') or 0
        s['out']+=t.get('output_tokens',0); s['think']+=t.get('thinking_tokens',0); s['win']=max(s['win'],W(t))
        ts=os.stat(f).st_mtime; s['first']=ts if s['first'] is None else min(s['first'],ts); s['last']=ts if s['last'] is None else max(s['last'],ts)
        if me.get('stop_reason')=='max_tokens': s['maxtok']+=1
        txt=me.get('text') or ''
        if '[agent:task done' in txt: s['done']+=1
        for tu in me.get('tool_uses') or []:
            if not isinstance(tu,dict): continue
            nm=tu.get('name'); s['tools'][nm]+=1; inp=tu.get('input') or {}
            if nm in('Edit','Write','MultiEdit'): s['edits'].add(inp.get('file_path'))
            if nm=='Bash':
                c=inp.get('command','')
                if re.search(r'\b(npm test|node --test|pytest|vitest|jest|npm run test|\.test\.js)',c): s['tests']+=1
                if re.search(r"sed -i|>>?\s*[\w./-]+\.(js|py|md|json|ts)|cat >|python3? - <<|apply_patch|git apply",c): s['bash_edits']+=1
    # tool errors + user turns from the last request body
    rq=fs[-1].replace('.response.json','.request.json')
    try:
        r=json.load(open(rq)); b=r.get('body') or {}
        msgs=b.get('messages') or []
        s['turns']=sum(1 for m in msgs if m.get('role')=='user' and (isinstance(m.get('content'),str) or any(x.get('type')=='text' for x in m['content'] if isinstance(x,dict))))
        for m in msgs:
            if m.get('role')=='user' and isinstance(m.get('content'),list):
                for x in m['content']:
                    if isinstance(x,dict) and x.get('type')=='tool_result':
                        if x.get('is_error'): s['errs']+=1
                        c=x.get('content'); c=c if isinstance(c,str) else ' '.join(y.get('text','') for y in c if isinstance(y,dict)) if isinstance(c,list) else ''
                        if re.search(r'\b(\d+) (failing|failed)\b|FAIL |AssertionError|not ok \d',c): s['fails']+=1
        s['compaction']=sum(1 for m in msgs if m.get('role')=='user' and isinstance(m.get('content'),str) and 'This session is being continued' in m['content'])
    except Exception as e: pass
rows=sorted(seats.items())
print(f"{'tkt':>5} sess     model       req  min  turns usd    out   think  maxwin  Read Grep Bash Edit  files tests fails errs  compact done")
by=collections.defaultdict(list)
for (t,sid),s in rows:
    if s['n']<3: continue
    m=s['model'].most_common(1)[0][0].replace('claude-',''); by[m].append(s)
    tl=s['tools']
    print(f"t{t:<4} {sid} {m:10} {s['n']:4d} {(s['last']-s['first'])/60:5.0f} {s['turns']:4d} {s['usd']:6.2f} {s['out']:6d} {s['think']:6d} {s['win']:7d} {tl.get('Read',0):5d}{tl.get('Grep',0):5d}{tl.get('Bash',0):5d}{tl.get('Edit',0)+tl.get('Write',0):5d} {len(s['edits']):5d} {s['tests']:5d} {s['fails']:5d} {s['errs']:4d} {s['compaction']:5d} {s['done']:4d}")
print()
for m,L in by.items():
    med=lambda f: st.median([f(x) for x in L]); mean=lambda f: st.mean([f(x) for x in L])
    print(f"{m}: seats={len(L)} | req p50={med(lambda x:x['n'])} mean={mean(lambda x:x['n']):.0f} | min p50={med(lambda x:(x['last']-x['first'])/60):.0f} | turns p50={med(lambda x:x['turns'])} | usd p50={med(lambda x:x['usd']):.2f} mean={mean(lambda x:x['usd']):.2f} | files edited p50={med(lambda x:len(x['edits']))} | edits p50={med(lambda x:x['tools'].get('Edit',0)+x['tools'].get('Write',0))} | test runs p50={med(lambda x:x['tests'])} | fails p50={med(lambda x:x['fails'])} | tool errs p50={med(lambda x:x['errs'])} | think/out={sum(x['think'] for x in L)/max(1,sum(x['out'] for x in L)):.2f} | req per file edited={sum(x['n'] for x in L)/max(1,sum(len(x['edits']) for x in L)):.1f}")
