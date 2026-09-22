import json,os,re,glob,time,statistics as st,collections
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
cut=time.time()-120*3600
RE=re.compile(r'clodex-clodex\.t(\d+)\.(hand|review-r(\d+))-')
def W(t): return sum((t.get(k) or 0) for k in ('input_tokens','cache_read_input_tokens','cache_write_5m_tokens','cache_write_1h_tokens'))
tick=collections.defaultdict(lambda:dict(rounds=set(),hands=[]))
for d in os.listdir(LD):
    p=os.path.join(LD,d)
    if d.startswith('_') or not os.path.isdir(p) or os.stat(p).st_mtime<cut: continue
    fs=[f for f in sorted(glob.glob(p+'/*-parent-*.request.json'),key=lambda f:int(os.path.basename(f).split('-')[0])) if RE.search(f)]
    if not fs: continue
    m=RE.search(fs[0]); t_=int(m.group(1))
    if m.group(2)!='hand': tick[t_]['rounds'].add(int(m.group(3))); continue
    n=0; usd=0; first=last=None; model=None
    for f in fs:
        rf=f.replace('.request.json','.response.json')
        if not os.path.exists(rf): continue
        o=json.load(open(rf))
        if o.get('status_code')!=200: continue
        t={k:(v or 0) for k,v in ((o.get('billing') or {}).get('tokens') or {}).items()}
        if W(t)<2000: continue
        n+=1; usd+=(o.get('billing') or {}).get('est_usd') or 0; model=o.get('model')
        ts=os.stat(rf).st_mtime; first=ts if first is None else min(first,ts); last=ts if last is None else max(last,ts)
    if n<3: continue
    b=(json.load(open(fs[-1])).get('body') or {}); msgs=b.get('messages') or []
    tools=collections.Counter(); edits=set(); errs=0; fails=0; tests=0; turns=0; bash_edits=0; rounds_of_tools=0; multi=0; reads_same=collections.Counter()
    for mm in msgs:
        c=mm.get('content')
        if mm.get('role')=='user':
            if isinstance(c,str) or (isinstance(c,list) and any(x.get('type')=='text' for x in c if isinstance(x,dict))): turns+=1
            if isinstance(c,list):
                for x in c:
                    if isinstance(x,dict) and x.get('type')=='tool_result':
                        if x.get('is_error'): errs+=1
                        cc=x.get('content'); cc=cc if isinstance(cc,str) else ' '.join(y.get('text','') for y in cc if isinstance(y,dict)) if isinstance(cc,list) else ''
                        if re.search(r'\b[1-9]\d* (failing|failed)\b|\bnot ok \d|AssertionError|Error: expected',cc): fails+=1
        elif mm.get('role')=='assistant' and isinstance(c,list):
            tus=[x for x in c if isinstance(x,dict) and x.get('type')=='tool_use']
            if tus: rounds_of_tools+=1
            if len(tus)>1: multi+=1
            for x in tus:
                nm=x.get('name'); tools[nm]+=1; inp=x.get('input') or {}
                if nm in('Edit','Write','MultiEdit','NotebookEdit'): edits.add(inp.get('file_path') or inp.get('notebook_path'))
                if nm=='Read': reads_same[inp.get('file_path')]+=1
                if nm=='Bash':
                    cmd=inp.get('command','')
                    if re.search(r'\b(npm test|node --test|pytest|vitest|jest|npm run test|--test )',cmd): tests+=1
                    if re.search(r"sed -i|cat >|python3? - <<|apply_patch|git apply|>\s*[\w./-]+\.(js|py|md|json|ts|css|html)\b",cmd): bash_edits+=1
    tick[t_]['hands'].append(dict(sid=d[:8],model=model,n=n,usd=usd,min=(last-first)/60,turns=turns,tools=tools,edits=len(edits),errs=errs,fails=fails,tests=tests,bash_edits=bash_edits,multi=multi,rounds=rounds_of_tools,rereads=sum(v-1 for v in reads_same.values() if v>1),nmsgs=len(msgs)))
rows=[]
print(f"{'tkt':>5} model   req  min turns rev  Read Grep Bash Edit bashEd files tests fails errs rereads multi% req/edit")
for t_ in sorted(tick):
    for h in tick[t_]['hands']:
        tl=h['tools']; rv=max(tick[t_]['rounds']) if tick[t_]['rounds'] else 0
        ed=tl.get('Edit',0)+tl.get('Write',0)+tl.get('MultiEdit',0)
        rows.append(dict(t=t_,rev=rv,**h,ed=ed))
        print(f"t{t_:<4} {h['model'][-6:]:7} {h['n']:4d} {h['min']:4.0f} {h['turns']:4d} {rv:3d}  {tl.get('Read',0):4d} {tl.get('Grep',0)+tl.get('Glob',0):4d} {tl.get('Bash',0):4d} {ed:4d} {h['bash_edits']:5d} {h['edits']:5d} {h['tests']:5d} {h['fails']:5d} {h['errs']:4d} {h['rereads']:6d} {100*h['multi']/max(1,h['rounds']):5.0f} {h['n']/max(1,ed+h['bash_edits']):6.1f}")
print()
med=lambda k: st.median([r[k] for r in rows]); tot=lambda k: sum(r[k] for r in rows)
print(f"hands={len(rows)} req p50={med('n')} mean={tot('n')/len(rows):.0f} | min p50={med('min'):.0f} | turns p50={med('turns')} | review rounds p50={med('rev')} mean={tot('rev')/len(rows):.2f} | edits p50={med('ed')} bash-edits p50={med('bash_edits')} files p50={med('edits')} | tests p50={med('tests')} fail-results p50={med('fails')} tool-errs p50={med('errs')} rereads p50={med('rereads')} | req/turn={tot('n')/tot('turns'):.1f} | req per edit op={tot('n')/(tot('ed')+tot('bash_edits')):.1f} | multi-tool rounds={100*tot('multi')/tot('rounds'):.0f}%")
rv=collections.Counter(r['rev'] for r in rows); print("review rounds distribution:",dict(sorted(rv.items())))
