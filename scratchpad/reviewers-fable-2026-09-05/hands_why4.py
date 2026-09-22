import json,os,re,glob,time,collections,datetime as dt,statistics as st
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
cut=time.time()-14*86400
RE=re.compile(r'clodex-clodex\.t(\d+)\.hand-')
CATS=[('gate:comment-ratchet',re.compile(r'comment-ratchet')),('gate:boundary-check',re.compile(r'boundary-check')),
 ('gate:other-suite',re.compile(r'free-identifier-leaks|api-contract|web-dist|drawer-services-seam|plugin-scope|test-failures/last\.txt')),
 ('sweep:comment-grep',re.compile(r"pinned by|covered by|\\bt\[0-9\]|\[A-Z\]\{2,\}|:\[0-9\]\{3,\}|grep[^|]*(-n[^|]*)?['\"]\s*//|grep[^|]*'//'|grep -c '//'|comment lines|docs/notes")),
 ('review:hunk-U20+',re.compile(r'git diff[^|;&]*(-U(2[0-9]|[3-9][0-9])|--unified=(2[0-9]|[3-9][0-9]))')),
 ('redproof',re.compile(r'git stash|git checkout --|git checkout HEAD --|git revert|git restore')),
 ('wait:sleep',re.compile(r'\bsleep\b')),
 ('test:node',re.compile(r'node --test|npm (test|run)|npx (vitest|jest|mocha)')),
]
TU_RE=re.compile(r'\[agent:task done')
out=collections.defaultdict(list)
for d in os.listdir(LD):
    p=os.path.join(LD,d)
    if d.startswith('_') or not os.path.isdir(p) or os.stat(p).st_mtime<cut: continue
    fs=[f for f in sorted(glob.glob(p+'/*.request.json'),key=lambda f:int(os.path.basename(f).split('-')[0])) if RE.search(f) and '-parent-' in f]
    if len(fs)<3: continue
    t_=int(RE.search(fs[0]).group(1)); first=min(os.stat(f).st_mtime for f in fs); day=dt.datetime.fromtimestamp(first).strftime('%m-%d')
    try: b=json.load(open(fs[-1]))['body']
    except: continue
    results={}
    for msg in b['messages']:
        c=msg['content']
        if msg['role']=='user' and isinstance(c,list):
            for x in c:
                if x.get('type')=='tool_result':
                    cc=x.get('content'); results[x.get('tool_use_id')]=len(cc) if isinstance(cc,str) else sum(len(y.get('text','')) for y in cc if isinstance(y,dict))
    seen=set(); cnt=collections.Counter(); vol=collections.Counter(); nb=0; done=False; pre=collections.Counter(); post=collections.Counter(); ntu=0
    for msg in b['messages']:
        c=msg['content']
        if msg['role']!='assistant' or not isinstance(c,list): continue
        for x in c:
            if x.get('type')=='text' and TU_RE.search(x.get('text','')): done=True
            if x.get('type')!='tool_use' or x['id'] in seen: continue
            seen.add(x['id']); ntu+=1
            (post if done else pre)['all']+=1
            if x['name']!='Bash': continue
            nb+=1; cmd=(x.get('input') or {}).get('command','')
            hit=False
            for k,rx in CATS:
                if rx.search(cmd):
                    cnt[k]+=1; vol[k]+=results.get(x['id'],0); hit=True
                    (post if done else pre)[k]+=1
                    break
            if not hit: cnt['plain']+=1; vol['plain']+=results.get(x['id'],0)
    out[day].append(dict(t=t_,nb=nb,ntu=ntu,cnt=cnt,vol=vol,pre=pre,post=post))
keys=[k for k,_ in CATS]+['plain']
print("per-hand mean BASH calls by category")
print("day    n  bash | "+' '.join(f"{k[:14]:>14s}" for k in keys))
for day in sorted(out):
    L=out[day]; print(f"{day} {len(L):3d} {st.mean(r['nb'] for r in L):5.0f} | "+' '.join(f"{st.mean(r['cnt'].get(k,0) for r in L):14.1f}" for k in keys))
print("\nper-hand mean RESULT kchars by category")
for day in sorted(out):
    L=out[day]; print(f"{day} {len(L):3d} {st.mean(sum(r['vol'].values()) for r in L)/1000:5.0f} | "+' '.join(f"{st.mean(r['vol'].get(k,0) for r in L)/1000:14.1f}" for k in keys))
print("\ncompliance share (gate:cr + boundary + sweep + hunk + redproof) of bash calls; and tool calls before/after first task-done")
for day in sorted(out):
    L=out[day]; comp=sum(r['cnt'].get(k,0) for r in L for k in ('gate:comment-ratchet','gate:boundary-check','sweep:comment-grep','review:hunk-U20+','redproof'))
    nb=sum(r['nb'] for r in L); pre=sum(r['pre']['all'] for r in L); post=sum(r['post']['all'] for r in L)
    print(f"{day}  compliance {comp:4d}/{nb:5d} = {100*comp/max(nb,1):4.1f}%   pre-done tools/hand {pre/len(L):5.0f}  post-done {post/len(L):5.0f}  ({100*post/max(pre+post,1):.0f}% rework)")
