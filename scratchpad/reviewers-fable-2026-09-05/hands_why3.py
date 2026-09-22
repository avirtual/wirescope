import json,os,re,glob,time,collections,datetime as dt,statistics as st
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
cut=time.time()-14*86400
RE=re.compile(r'clodex-clodex\.t(\d+)\.hand-')
TEST_RE=re.compile(r'\b(npm (test|run test\w*)|npx (vitest|jest|mocha|tsc|eslint)|node --test|pytest|python3? -m (pytest|unittest))\b')
GATES=['comment-ratchet','free-identifier-leaks','boundary-check','api-contract','drawer-services-seam','web-dist','plugin-scope','test-failures/last.txt']
FAIL_RE=re.compile(r'(^not ok|✖|AssertionError|FAIL|Error:|failing)',re.M)
day_rows=collections.defaultdict(list); roles={}
for d in os.listdir(LD):
    p=os.path.join(LD,d)
    if d.startswith('_') or not os.path.isdir(p) or os.stat(p).st_mtime<cut: continue
    fs=[f for f in sorted(glob.glob(p+'/*.request.json'),key=lambda f:int(os.path.basename(f).split('-')[0])) if RE.search(f) and '-parent-' in f]
    if len(fs)<3: continue
    t_=int(RE.search(fs[0]).group(1)); first=min(os.stat(f).st_mtime for f in fs); day=dt.datetime.fromtimestamp(first).strftime('%m-%d')
    try: b=json.load(open(fs[-1]))['body']
    except: continue
    role=[s.get('text','') for s in b.get('system',[]) if s.get('text','').startswith('# Team hand')]
    if role: roles.setdefault(day,role[0])
    results={}; 
    for msg in b['messages']:
        c=msg['content']
        if msg['role']=='user' and isinstance(c,list):
            for x in c:
                if x.get('type')=='tool_result':
                    cc=x.get('content'); s=cc if isinstance(cc,str) else ' '.join(y.get('text','') for y in cc if isinstance(y,dict)); results[x.get('tool_use_id')]=(s,bool(x.get('is_error')))
    seen=set(); vol=collections.Counter(); tests=0; tfail=0; gates=collections.Counter(); tu=0; sleeps=0; gate_runs=0
    for msg in b['messages']:
        c=msg['content']
        if msg['role']=='assistant' and isinstance(c,list):
            for x in c:
                if x.get('type')!='tool_use' or x['id'] in seen: continue
                seen.add(x['id']); tu+=1
                r=results.get(x['id'],('',False)); n=x['name']; inp=x.get('input') or {}
                if n=='Bash':
                    cmd=inp.get('command','')
                    if TEST_RE.search(cmd):
                        tests+=1; k='Bash:test'
                        if FAIL_RE.search(r[0]) or r[1]: tfail+=1
                        if any(g in cmd for g in GATES): gate_runs+=1
                    elif 'sleep' in cmd: sleeps+=1; k='Bash:sleep'
                    else: k='Bash:other'
                    for g in GATES:
                        if g in cmd: gates[g]+=1
                else: k=n
                vol[k]+=len(r[0])
    day_rows[day].append(dict(t=t_,tu=tu,tests=tests,tfail=tfail,gate_runs=gate_runs,gates=gates,vol=vol,sleeps=sleeps,total=sum(vol.values())))
print("day    n  tools  tests  fail  gate-runs  sleeps | result chars/hand: total  Bash:test  Bash:other  Read  | gates seen")
for day in sorted(day_rows):
    L=day_rows[day]; m=lambda k:st.mean(r[k] for r in L); v=lambda k:st.mean(r['vol'].get(k,0) for r in L)
    g=collections.Counter()
    for r in L: g.update(r['gates'])
    print(f"{day} {len(L):3d} {m('tu'):6.0f} {m('tests'):5.0f} {m('tfail'):5.0f} {m('gate_runs'):7.0f} {m('sleeps'):6.0f} | {m('total')/1000:8.0f}k {v('Bash:test')/1000:8.0f}k {v('Bash:other')/1000:8.0f}k {v('Read')/1000:5.0f}k | {dict(g.most_common(4))}")
json.dump({d:r for d,r in roles.items()},open('roles_by_day.json','w'))
