# Struggle signatures per hand: rework share (requests/time after first task-done), same-file edit churn,
# consecutive failing test runs, longest stretch between edits, and where the requests go by phase.
import json,os,re,glob,time,statistics as st,collections
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
cut=time.time()-120*3600
RE=re.compile(r'clodex-clodex\.t(\d+)\.hand-')
def W(t): return sum((t.get(k) or 0) for k in ('input_tokens','cache_read_input_tokens','cache_write_5m_tokens','cache_write_1h_tokens'))
rows=[]
for d in os.listdir(LD):
    p=os.path.join(LD,d)
    if d.startswith('_') or not os.path.isdir(p) or os.stat(p).st_mtime<cut: continue
    fs=[f for f in sorted(glob.glob(p+'/*-parent-*.response.json'),key=lambda f:int(os.path.basename(f).split('-')[0])) if RE.search(f)]
    if len(fs)<3: continue
    t_=int(RE.search(fs[0]).group(1))
    seq=[]  # (ts, done?, usd)
    for f in fs:
        o=json.load(open(f))
        if o.get('status_code')!=200: continue
        t={k:(v or 0) for k,v in ((o.get('billing') or {}).get('tokens') or {}).items()}
        if W(t)<2000: continue
        me=o.get('meta') or {}
        seq.append((os.stat(f).st_mtime,'[agent:task done' in (me.get('text') or ''),(o.get('billing') or {}).get('est_usd') or 0))
    if len(seq)<3: continue
    n=len(seq); dones=[i for i,s in enumerate(seq) if s[1]]
    first_done=dones[0] if dones else n-1
    rework_req=n-1-first_done; rework_usd=sum(s[2] for s in seq[first_done+1:]); usd=sum(s[2] for s in seq)
    rework_min=(seq[-1][0]-seq[first_done][0])/60; total_min=(seq[-1][0]-seq[0][0])/60
    # body-based churn
    b=json.load(open(fs[-1].replace('.response.json','.request.json'))).get('body') or {}
    msgs=b.get('messages') or []
    edit_files=collections.Counter(); test_seq=[]; since_edit=0; max_since=0; n_asst=0
    for i,m in enumerate(msgs):
        c=m.get('content')
        if m.get('role')=='assistant' and isinstance(c,list):
            n_asst+=1; edited=False; ran_test=False
            for x in c:
                if not (isinstance(x,dict) and x.get('type')=='tool_use'): continue
                inp=x.get('input') or {}
                if x.get('name') in('Edit','Write','MultiEdit'): edit_files[inp.get('file_path')]+=1; edited=True
                if x.get('name')=='Bash':
                    cmd=inp.get('command','')
                    if re.search(r"sed -i|cat >|python3? - <<|>\s*[\w./-]+\.(js|py|md|json|ts|css|html)\b",cmd): edited=True
                    if re.search(r'\b(npm test|node --test|pytest|vitest|jest|npm run test|--test )',cmd): ran_test=True
            since_edit=0 if edited else since_edit+1; max_since=max(max_since,since_edit)
            if ran_test:
                # look at the following user message for a failure signature
                nxt=msgs[i+1] if i+1<len(msgs) else {}
                txt=''
                if isinstance(nxt.get('content'),list):
                    for y in nxt['content']:
                        if isinstance(y,dict) and y.get('type')=='tool_result':
                            cc=y.get('content'); txt+=cc if isinstance(cc,str) else ' '.join(z.get('text','') for z in cc if isinstance(z,dict)) if isinstance(cc,list) else ''
                test_seq.append(bool(re.search(r'\b[1-9]\d* (failing|failed)\b|\bnot ok \d|AssertionError|Error: expected|FAIL\b',txt)))
    # longest run of consecutive failing test runs
    run=best=0
    for f_ in test_seq:
        run=run+1 if f_ else 0; best=max(best,run)
    churn=sum(v for v in edit_files.values() if v>=3); hot=max(edit_files.values()) if edit_files else 0
    rows.append(dict(t=t_,n=n,usd=usd,total_min=total_min,dones=len(dones),rework_req=rework_req,rework_usd=rework_usd,rework_min=rework_min,tests=len(test_seq),fails=sum(test_seq),fail_run=best,hot=hot,files=len(edit_files),max_since=max_since,n_asst=n_asst))
rows.sort(key=lambda r:r['t'])
print(f"{'tkt':>5} req  usd  dones  rework_req  rework%  rework$  tests fails maxfailrun  hottest-file-edits  files  longest-no-edit-run")
for r in rows:
    print(f"t{r['t']:<4} {r['n']:4d} {r['usd']:5.1f} {r['dones']:4d} {r['rework_req']:8d} {100*r['rework_req']/r['n']:7.0f}% {r['rework_usd']:7.2f} {r['tests']:5d} {r['fails']:5d} {r['fail_run']:8d} {r['hot']:12d} {r['files']:9d} {r['max_since']:10d}")
N=len(rows); T=lambda k: sum(r[k] for r in rows)
print(f"\nhands={N} | rework share: {100*T('rework_req')/T('n'):.0f}% of requests, ${T('rework_usd'):.0f} of ${T('usd'):.0f} ({100*T('rework_usd')/T('usd'):.0f}%) | hands with rework: {sum(1 for r in rows if r['rework_req']>0)} | dones p50={st.median([r['dones'] for r in rows])} | test runs p50={st.median([r['tests'] for r in rows])} fail rate={100*T('fails')/max(1,T('tests')):.0f}% | hands with a fail-run>=3: {sum(1 for r in rows if r['fail_run']>=3)} | hottest file edited >=5x: {sum(1 for r in rows if r['hot']>=5)} | longest no-edit run p50={st.median([r['max_since'] for r in rows])} p90={sorted(r['max_since'] for r in rows)[int(0.9*N)]}")
print("pre-first-done requests p50:",st.median([r['n']-r['rework_req'] for r in rows]),"| post p50:",st.median([r['rework_req'] for r in rows]))
