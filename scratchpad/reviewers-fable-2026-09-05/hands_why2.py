import json,os,re,glob,time,collections,datetime as dt,statistics as st
LD="/Users/bogdan/Library/Application Support/clodex/wirescope/logs"
cut=time.time()-14*86400
RE=re.compile(r'clodex-clodex\.t(\d+)\.hand-')
R={ (r['t'],r['day']):r for r in json.load(open('hands_why.json'))}
early=('08-26','08-27','08-28'); late=('09-04','09-05'); mid=('08-29','08-30','08-31')
heads={'early':collections.Counter(),'mid':collections.Counter(),'late':collections.Counter()}
lens={'early':[], 'mid':[], 'late':[]}
resv={'early':collections.Counter(),'mid':collections.Counter(),'late':collections.Counter()}
samples={'late':[], 'early':[]}
specs=collections.defaultdict(list); replays=collections.Counter(); doneidx=collections.defaultdict(list)
def head(cmd):
    c=cmd.strip()
    c=re.sub(r'^cd\s+\S+\s*&&\s*','',c)
    w=c.split()
    if not w: return '(empty)'
    h=w[0]
    if h in('node','python3','python','npx','npm','git','sed','grep','rg','cat','sqlite3','curl','ls','find','wc','head','tail','diff','test','bash','sh','for','while','if','echo','printf'):
        return h+(' '+w[1] if len(w)>1 and h in('git','npm','npx','node','python3','sed') and not w[1].startswith('-') else '')
    return h
for d in os.listdir(LD):
    p=os.path.join(LD,d)
    if d.startswith('_') or not os.path.isdir(p) or os.stat(p).st_mtime<cut: continue
    fs=[f for f in sorted(glob.glob(p+'/*.request.json'),key=lambda f:int(os.path.basename(f).split('-')[0])) if RE.search(f) and '-parent-' in f]
    if len(fs)<3: continue
    t_=int(RE.search(fs[0]).group(1)); first=min(os.stat(f).st_mtime for f in fs); day=dt.datetime.fromtimestamp(first).strftime('%m-%d')
    grp='early' if day in early else 'late' if day in late else 'mid' if day in mid else None
    try: b=json.load(open(fs[-1]))['body']
    except: continue
    m0=b['messages'][0]['content']
    txt=' '.join(x.get('text','') for x in m0 if isinstance(x,dict)) if isinstance(m0,list) else m0
    m=re.search(r'\[ticket t\d+([^\]]*)\].*?Message \((\d+) bytes\)',txt,re.S)
    if m: specs[day].append(int(m.group(2))); 
    if m and 'REPLAY' in m.group(1): replays[day]+=1
    # first task-done position
    n_tu=0; done_at=None; seen=set(); results={}
    for msg in b['messages']:
        c=msg['content']
        if msg['role']=='user' and isinstance(c,list):
            for x in c:
                if x.get('type')=='tool_result':
                    cc=x.get('content'); results[x.get('tool_use_id')]=len(cc) if isinstance(cc,str) else sum(len(y.get('text','')) for y in cc if isinstance(y,dict))
        if msg['role']=='assistant' and isinstance(c,list):
            for x in c:
                if x.get('type')=='text' and '[agent:task done' in x.get('text','') and done_at is None: done_at=n_tu
                if x.get('type')=='tool_use' and x['id'] not in seen:
                    seen.add(x['id']); n_tu+=1
                    if grp and x['name']=='Bash':
                        cmd=(x.get('input') or {}).get('command','')
                        heads[grp][head(cmd)]+=1; lens[grp].append(len(cmd))
                        resv[grp][head(cmd)]+=results.get(x['id'],0)
                        if len(cmd)<400 and len(samples.get(grp,[]))<60 and grp in samples and head(cmd) in('node','node -e','bash','for','sqlite3','curl'): samples[grp].append(cmd[:200].replace('\n','⏎'))
    doneidx[day].append((done_at,n_tu))
print("day    spec bytes mean/p50   replays   first-done at %-of-tools (mean)  hands w/o done")
for day in sorted(specs):
    L=doneidx[day]; frac=[a/b for a,b in L if a is not None and b]
    print(f"{day}  {st.mean(specs[day]):7.0f} {st.median(specs[day]):6.0f}   {replays[day]:3d}      {st.mean(frac) if frac else 0:5.2f}                         {sum(1 for a,b in L if a is None)}")
for g in('early','mid','late'):
    tot=sum(heads[g].values()); print(f"\n{g}: {tot} bash calls, cmd len p50 {st.median(lens[g]):.0f} mean {st.mean(lens[g]):.0f}; result chars total {sum(resv[g].values())/1e6:.2f}M")
    for h,n in heads[g].most_common(22): print(f"   {h:16s} {n:5d} {100*n/tot:5.1f}%  result chars/call {resv[g][h]/max(n,1):7.0f}")
print("\nlate samples (node/bash/for/sqlite3/curl):")
for s in samples['late'][:40]: print('  ',s)
