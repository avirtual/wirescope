import json,os,re,sys,glob,statistics as st,time
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
HOURS=float(sys.argv[1]) if len(sys.argv)>1 else 30
cut=time.time()-HOURS*3600
RE=re.compile(r'clodex-clodex\.t(\d+)\.review-r(\d+)-')
VERD=re.compile(r'\b(ACCEPT(?:ED)?|APPROVE[D]?|REWORK|REJECT(?:ED)?|MUST-FIX|LGTM|PASS|FAIL)\b',re.I)
seats={}
for d in os.listdir(LD):
    p=os.path.join(LD,d)
    if d.startswith('_') or not os.path.isdir(p) or os.stat(p).st_mtime<cut: continue
    for f in sorted(glob.glob(p+'/*.response.json')):
        m=RE.search(f)
        if not m: continue
        try: o=json.load(open(f))
        except Exception: continue
        key=(int(m.group(1)),int(m.group(2)),d[:8])
        s=seats.setdefault(key,{'model':None,'n':0,'usd':0,'out':0,'think':0,'win':[],'first':None,'last':None,'tools':{},'ntools':0,'stops':{},'last_text':'','cp':None,'sysh':None,'verd':[],'maxtok':0})
        b=o.get('billing') or {}; t=b.get('tokens') or {}; me=o.get('meta') or {}
        s['model']=o.get('model') or s['model']
        s['n']+=1; s['usd']+=b.get('est_usd') or 0
        s['out']+=t.get('output_tokens') or 0; s['think']+=t.get('thinking_tokens') or 0
        w=(t.get('input_tokens') or 0)+(t.get('cache_read_input_tokens') or 0)+(t.get('cache_write_5m_tokens') or 0)+(t.get('cache_write_1h_tokens') or 0)
        if w: s['win'].append(w)
        sr=me.get('stop_reason'); s['stops'][sr]=s['stops'].get(sr,0)+1
        if sr=='max_tokens': s['maxtok']+=1
        for tu in (me.get('tool_uses') or []):
            nm=tu.get('name') if isinstance(tu,dict) else str(tu)
            s['tools'][nm]=s['tools'].get(nm,0)+1; s['ntools']+=1
        txt=me.get('text') or ''
        if sr=='end_turn' and len(txt)>len(s['last_text']): s['last_text']=txt
        vs=VERD.findall(txt)
        if sr=='end_turn' and vs: s['verd'].append(vs[-1].upper())
        mt=os.stat(f).st_mtime
        s['first']=mt if s['first'] is None else min(s['first'],mt); s['last']=mt if s['last'] is None else max(s['last'],mt)
        if s['cp'] is None:
            rq=f.replace('.response.json','.request.json')
            try:
                r=json.load(open(rq)); body=r.get('body') or r
                if body.get('tools'):
                    s['cp']={'effort':(body.get('output_config') or {}).get('effort'),'thinking':(body.get('thinking') or {}).get('type'),'budget':(body.get('thinking') or {}).get('budget_tokens'),'max_tokens':body.get('max_tokens'),'ntools':len(body.get('tools') or [])}
                    sy=body.get('system'); sy=sy if isinstance(sy,str) else ' '.join(x.get('text','') for x in sy if isinstance(x,dict))
                    import hashlib
                    msg0=body['messages'][0]['content']; msg0=msg0 if isinstance(msg0,str) else ' '.join(x.get('text','') for x in msg0 if isinstance(x,dict) and x.get('type')=='text')
                    s['sysh']=(len(sy),hashlib.md5(sy.encode()).hexdigest()[:6],len(msg0))
                    s['msg0']=msg0
            except Exception as e: s['cp']={'err':str(e)}
rows=[(k,s) for k,s in sorted(seats.items()) if s['n']>=3]
print(f"{'ticket':>6} {'r':>2} sess     model      n  usd   out   think  win1st  winlast  min  tools  Read Grep Bash Edit  verdict           cp")
by={}
for (t,r,sid),s in rows:
    m=(s['model'] or '?').replace('claude-','')
    by.setdefault(m,[]).append(s)
    tl=s['tools']
    print(f"t{t:<4} r{r:<2} {sid} {m:9} {s['n']:3d} {s['usd']:5.2f} {s['out']:6d} {s['think']:6d} {s['win'][0] if s['win'] else 0:7d} {s['win'][-1] if s['win'] else 0:8d} {(s['last']-s['first'])/60:5.1f} {s['ntools']:5d} {tl.get('Read',0):5d}{tl.get('Grep',0):5d}{tl.get('Bash',0):5d}{tl.get('Edit',0)+tl.get('Write',0):5d}  {','.join(s['verd'][-2:]):17} {s['cp']} sys={s['sysh']}")
print()
def med(L,f): return st.median([f(x) for x in L])
for m,L in by.items():
    for label,sub in (('all',L),('r1',[x for x in L if True])):
        pass
    print(f"{m}: seats={len(L)} req p50={med(L,lambda x:x['n'])} | usd p50={med(L,lambda x:x['usd']):.2f} tot={sum(x['usd'] for x in L):.2f} | out p50={med(L,lambda x:x['out'])} | think/out={sum(x['think'] for x in L)/max(1,sum(x['out'] for x in L)):.2f} | win1st p50={med(L,lambda x:x['win'][0])} winlast p50={med(L,lambda x:x['win'][-1])} growth p50={med(L,lambda x:x['win'][-1]-x['win'][0])} | min p50={med(L,lambda x:(x['last']-x['first'])/60):.1f} | tools p50={med(L,lambda x:x['ntools'])} | usd/req={sum(x['usd'] for x in L)/sum(x['n'] for x in L):.3f} | sec/req={sum((x['last']-x['first']) for x in L)/sum(x['n'] for x in L):.0f} | out/req={sum(x['out'] for x in L)/sum(x['n'] for x in L):.0f} | verdict_len p50={med(L,lambda x:len(x['last_text']))}")
json.dump({f"t{k[0]}r{k[1]}-{k[2]}":{'model':s['model'],'last_text':s['last_text'],'msg0':s.get('msg0','')[:6000],'sysh':s['sysh']} for k,s in rows},open('/Users/bogdan/projects/proxy-lab/scratchpad/reviewers-fable-2026-09-05/texts.json','w'))
